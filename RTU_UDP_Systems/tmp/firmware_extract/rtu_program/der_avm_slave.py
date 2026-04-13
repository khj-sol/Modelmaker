#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DER-AVM Modbus Slave Handler
Version: 3.0.0

Implements Modbus RTU Slave for DER-AVM communication.
Supports: CM4 native UART (V3.0.0) / RS485 HAT CH2 (legacy)

- Supports multiple inverters (Slave ID 1, 2, 3, ...)
- Responds to read requests (FC03) for real-time data per inverter
- Processes write requests (FC06/FC16) for control commands per inverter
- Monitors communication timeout (1 minute)
- Forwards control commands to specific inverter via callback

Changes in 3.0.0:
- Added CM4-ETH-RS485-BASE-B native UART support (pyserial)
- CM4 mode uses RS485ChannelSerial on COM2 (/dev/ttyAMA4)
- No HAT transaction lock needed for CM4 (independent UART ports)

Changes in 1.1.0:
- Added multi-inverter support (inverter_count parameter)
- Per-inverter data storage (_inverter_data[id])
- Per-inverter control parameters (_control_params[id])
- DER-AVM Master can poll each inverter by Slave ID
- Control commands target specific inverter by Slave ID

Changes in 1.0.2:
- Added HAT transaction lock for CH2 priority over CH1
- Prevents SPI conflicts when responding to DER-AVM requests

Changes in 1.0.1:
- Fixed Waveshare HAT RS485 function names (Read vs read)
- Added RXLVL register check for available data
- Improved receive loop with proper IRQ handling
"""

import time
import logging
import struct
import threading
from typing import Callable, Dict, Optional, Tuple

from der_avm_registers import (
    DerAvmRegisters, DerAvmStatusFlag, DerAvmActionMode,
    DEFAULT_POWER_FACTOR, DEFAULT_ACTION_MODE,
    DEFAULT_REACTIVE_POWER_PCT, DEFAULT_ACTIVE_POWER_PCT,
    DEFAULT_INVERTER_OFF, DER_AVM_COMM_TIMEOUT
)

# Try to import CM4 native serial
CM4_SERIAL_AVAILABLE = False
try:
    import sys
    import os
    libdir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'lib')
    sys.path.append(libdir)
    from cm4_serial.rs485_channel_serial import RS485ChannelSerial
    from cm4_serial.config import get_serial_port
    CM4_SERIAL_AVAILABLE = True
except (ImportError, FileNotFoundError, OSError):
    pass

# Try to import HAT library (legacy)
HAT_AVAILABLE = False
GPIO_AVAILABLE = False
HAT_LOCK_AVAILABLE = False
try:
    if not CM4_SERIAL_AVAILABLE:
        import sys
        import os
        libdir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'lib')
    sys.path.append(libdir)
    hatdir = os.path.join(libdir, 'waveshare_2_CH_RS485_HAT')
    sys.path.append(hatdir)
    from RS485 import RS485
    HAT_AVAILABLE = True
    # Import HAT transaction lock
    from rs485_channel import acquire_hat_lock, release_hat_lock
    HAT_LOCK_AVAILABLE = True
except (ImportError, FileNotFoundError, OSError):
    pass

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    pass


class ModbusCRC:
    """Modbus CRC-16 calculation"""
    
    @staticmethod
    def calculate(data: bytes) -> int:
        """Calculate CRC-16 for Modbus RTU"""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc
    
    @staticmethod
    def append(data: bytes) -> bytes:
        """Append CRC to data"""
        crc = ModbusCRC.calculate(data)
        return data + struct.pack('<H', crc)
    
    @staticmethod
    def verify(data: bytes) -> bool:
        """Verify CRC of received data"""
        if len(data) < 4:
            return False
        received_crc = struct.unpack('<H', data[-2:])[0]
        calculated_crc = ModbusCRC.calculate(data[:-2])
        return received_crc == calculated_crc


class DerAvmSlave:
    """
    DER-AVM Modbus Slave Handler
    
    Runs on RS485 HAT CH2, responds to DER-AVM master requests.
    """
    
    # Modbus function codes
    FC_READ_HOLDING = 0x03
    FC_WRITE_SINGLE = 0x06
    FC_WRITE_MULTIPLE = 0x10
    
    # Modbus exception codes
    EX_ILLEGAL_FUNCTION = 0x01
    EX_ILLEGAL_ADDRESS = 0x02
    EX_ILLEGAL_VALUE = 0x03
    
    def __init__(self, slave_id: int = 1, baudrate: int = 9600,
                 control_callback: Callable = None,
                 simulation_mode: bool = False,
                 inverter_count: int = 3,
                 use_cm4: bool = False,
                 cm4_slave_port: str = None):
        """
        Initialize DER-AVM Slave

        Args:
            slave_id: Base Modbus slave ID (INV1=slave_id, INV2=slave_id+1, ...)
            baudrate: Serial baudrate (default 9600)
            control_callback: Callback function when control command received
                              callback(inverter_id, ctrl_type, value) -> bool (success)
            simulation_mode: If True, run without hardware
            inverter_count: Number of inverters to manage (1-10)
            use_cm4: If True, use CM4 native UART instead of HAT
            cm4_slave_port: Override CM4 slave port (default: COM2 /dev/ttyAMA4)
        """
        self.slave_id = slave_id  # Base slave ID
        self.inverter_count = inverter_count
        self.baudrate = baudrate
        self.control_callback = control_callback
        self.simulation_mode = simulation_mode
        self.use_cm4 = use_cm4
        self.cm4_slave_port = cm4_slave_port

        self.logger = logging.getLogger(__name__)
        self.rs485 = None
        self._irq_pin = None
        self._serial_ch = None  # CM4 serial channel
        self.running = False
        self.thread = None
        
        # Per-inverter real-time data storage
        # Key: inverter_id (1, 2, 3, ...)
        self._inverter_data = {}
        for inv_id in range(1, inverter_count + 1):
            self._inverter_data[inv_id] = {
                'l1_current': 0,      # 0.1A
                'l2_current': 0,
                'l3_current': 0,
                'l1_voltage': 0,      # 0.1V
                'l2_voltage': 0,
                'l3_voltage': 0,
                'active_power': 0,    # 0.1kW
                'reactive_power': 0,  # Var
                'power_factor': 1000, # 0.001
                'frequency': 600,     # 0.1Hz (60.0Hz)
                'simulation': False,  # True if simulation mode
            }
        
        # Legacy: _realtime_data for backwards compatibility (aggregated)
        self._realtime_data = self._inverter_data.get(1, {})
        
        # Status flag manager (per-inverter)
        self._status_flags = {inv_id: DerAvmStatusFlag() for inv_id in range(1, inverter_count + 1)}
        self.status_flag = self._status_flags.get(1, DerAvmStatusFlag())  # Legacy
        
        # Control parameter storage (per-inverter)
        self._control_params = {}
        for inv_id in range(1, inverter_count + 1):
            self._control_params[inv_id] = {
                DerAvmRegisters.POWER_FACTOR_SET: DEFAULT_POWER_FACTOR,
                DerAvmRegisters.ACTION_MODE: DEFAULT_ACTION_MODE,
                DerAvmRegisters.REACTIVE_POWER_PCT: DEFAULT_REACTIVE_POWER_PCT,
                DerAvmRegisters.ACTIVE_POWER_PCT: DEFAULT_ACTIVE_POWER_PCT,
                DerAvmRegisters.INVERTER_OFF: DEFAULT_INVERTER_OFF,
            }
        
        # Communication monitoring
        self._last_read_time = 0
        self._comm_error = False
        self._read_count = 0
        self._write_count = 0
        self._error_count = 0
        
        # Thread lock for data access
        self._lock = threading.Lock()
    
    def start(self) -> bool:
        """Start DER-AVM slave service"""
        if self.simulation_mode:
            self.logger.info("[DER-AVM] Simulation mode enabled")
            self._last_read_time = time.time()
            self.running = True
            return True

        # CM4 native UART mode
        if self.use_cm4:
            if not CM4_SERIAL_AVAILABLE:
                self.logger.error("[DER-AVM] CM4 serial driver not available")
                return False

            try:
                port = self.cm4_slave_port or get_serial_port(2)  # COM2
                self._serial_ch = RS485ChannelSerial(
                    port=port,
                    baudrate=self.baudrate,
                    channel_num=2
                )

                self.logger.info(
                    f"[DER-AVM] Started on CM4 COM2 ({port}) "
                    f"@ {self.baudrate}bps (Slave ID={self.slave_id})"
                )

                self._last_read_time = time.time()
                self.running = True

                self.thread = threading.Thread(target=self._receive_loop_cm4, daemon=True)
                self.thread.start()
                return True

            except Exception as e:
                self.logger.error(f"[DER-AVM] CM4 start failed: {e}")
                import traceback
                traceback.print_exc()
                return False

        # Legacy HAT mode
        if not HAT_AVAILABLE:
            self.logger.error("[DER-AVM] RS485 HAT not available")
            return False

        try:
            self.rs485 = RS485()
            self.rs485.RS485_CH2_begin(self.baudrate)

            # Get IRQ pin for data available check
            if hasattr(self.rs485, 'SC16IS752_CH2'):
                self._irq_pin = self.rs485.SC16IS752_CH2.IRQ_PIN
            else:
                self._irq_pin = None

            self.logger.info(f"[DER-AVM] Started on HAT CH2 @ {self.baudrate}bps (Slave ID={self.slave_id})")

            self._last_read_time = time.time()
            self.running = True

            # Start receive thread
            self.thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.thread.start()

            return True

        except Exception as e:
            self.logger.error(f"[DER-AVM] Start failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def stop(self):
        """Stop DER-AVM slave service"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        if self._serial_ch:
            self._serial_ch.close()
            self._serial_ch = None
        self.logger.info("[DER-AVM] Stopped")

    # =========================================================================
    # HAT-mode low-level I/O (legacy, uses SPI/SC16IS752)
    # =========================================================================

    def _get_rx_level(self) -> int:
        """Get RX FIFO level for CH2 using RXLVL register (HAT mode)"""
        try:
            CMD_READ = 0x80
            RXLVL = 0x09
            REG = lambda x: x << 3
            ch_offset = 0x02  # CHANNEL_2

            result = self.rs485.SC16IS752_CH2.WR_REG(CMD_READ | REG(RXLVL) | ch_offset, 0xFF)
            if result and len(result) > 0:
                return result[0]
            return 0
        except Exception as e:
            return 0

    def _has_data_available(self) -> bool:
        """Check if data is available in RX FIFO (HAT mode)"""
        return self._get_rx_level() > 0

    def _read_byte(self) -> Optional[int]:
        """Read single byte from CH2 (HAT mode)"""
        try:
            data = self.rs485.RS485_CH2_ReadByte()
            if data:
                return ord(data) if isinstance(data, str) else data
            return None
        except Exception as e:
            self.logger.debug(f"[DER-AVM] Read byte error: {e}")
            return None
    
    def _read_bytes(self, length: int) -> bytes:
        """Read multiple bytes from CH2"""
        try:
            data = self.rs485.RS485_CH2_Read(length)
            if isinstance(data, str):
                return bytes([ord(c) for c in data])
            return bytes(data) if data else b''
        except Exception as e:
            self.logger.debug(f"[DER-AVM] Read bytes error: {e}")
            return b''
    
    def _receive_loop(self):
        """Main receive loop for Modbus requests"""
        self.logger.info("[DER-AVM] Receive loop started")
        buffer = bytearray()
        last_byte_time = 0
        
        while self.running:
            try:
                # Check if data is available
                if self._has_data_available():
                    # Read one byte
                    byte_val = self._read_byte()
                    if byte_val is not None:
                        buffer.append(byte_val)
                        last_byte_time = time.time()
                        self.logger.debug(f"[DER-AVM] RX byte: 0x{byte_val:02X}, buffer len={len(buffer)}")
                else:
                    # No data available
                    if len(buffer) > 0:
                        # Check frame timeout (3.5 character times at 9600 = ~4ms)
                        if time.time() - last_byte_time > 0.05:
                            # Frame complete, try to process
                            if len(buffer) >= 4:
                                self.logger.debug(f"[DER-AVM] Frame complete: {buffer.hex().upper()}")
                                self._process_buffer(buffer)
                            else:
                                self.logger.debug(f"[DER-AVM] Discarding short frame: {buffer.hex().upper()}")
                            buffer.clear()
                    else:
                        # Sleep when no data
                        time.sleep(0.001)
                
            except Exception as e:
                self.logger.error(f"[DER-AVM] Receive error: {e}")
                buffer.clear()
                time.sleep(0.1)
        
        self.logger.info("[DER-AVM] Receive loop ended")

    # =========================================================================
    # CM4-mode receive loop (native UART via pyserial)
    # =========================================================================

    def _receive_loop_cm4(self):
        """Main receive loop for CM4 native UART (pyserial)"""
        self.logger.info("[DER-AVM] CM4 receive loop started")
        buffer = bytearray()
        last_byte_time = 0

        while self.running:
            try:
                # Check if data is available
                if self._serial_ch.get_rx_level() > 0:
                    byte_char = self._serial_ch.read_byte()
                    if byte_char is not None:
                        buffer.append(ord(byte_char))
                        last_byte_time = time.time()
                else:
                    if len(buffer) > 0:
                        if time.time() - last_byte_time > 0.05:
                            if len(buffer) >= 4:
                                self.logger.debug(f"[DER-AVM] Frame: {buffer.hex().upper()}")
                                self._process_buffer(buffer)
                            buffer.clear()
                    else:
                        time.sleep(0.001)

            except Exception as e:
                self.logger.error(f"[DER-AVM] CM4 receive error: {e}")
                buffer.clear()
                time.sleep(0.1)

        self.logger.info("[DER-AVM] CM4 receive loop ended")

    def _process_buffer(self, buffer: bytearray):
        """Process received buffer as Modbus frame"""
        # Check slave ID range (slave_id to slave_id + inverter_count - 1)
        request_slave_id = buffer[0]
        max_slave_id = self.slave_id + self.inverter_count - 1

        if request_slave_id < self.slave_id or request_slave_id > max_slave_id:
            self.logger.debug(f"[DER-AVM] Not for us: slave_id={request_slave_id}, valid range={self.slave_id}-{max_slave_id}")
            return

        # Calculate inverter ID (1-based)
        inverter_id = request_slave_id - self.slave_id + 1

        # Verify CRC
        if not ModbusCRC.verify(bytes(buffer)):
            self.logger.warning(f"[DER-AVM] CRC error: {buffer.hex().upper()}")
            self._error_count += 1
            return

        # Acquire HAT lock only in HAT mode (not needed for CM4)
        lock_acquired = False
        if not self.use_cm4 and HAT_LOCK_AVAILABLE:
            lock_acquired = acquire_hat_lock(2, timeout=5.0)

        try:
            self._process_request(bytes(buffer), inverter_id)
        finally:
            if not self.use_cm4 and HAT_LOCK_AVAILABLE and lock_acquired:
                release_hat_lock(2)
    
    def update_realtime_data(self, data: Dict, inverter_id: int = 1):
        """
        Update real-time data from inverter readings (legacy, single inverter)
        
        Args:
            data: Dictionary with inverter data
            inverter_id: Inverter ID (1, 2, 3, ...)
        """
        self.update_inverter_data(inverter_id, data)
    
    def update_inverter_data(self, inverter_id: int, data: Dict, simulation: bool = False):
        """
        Update real-time data for specific inverter
        
        Args:
            inverter_id: Inverter ID (1, 2, 3, ...)
            data: Dictionary with inverter data
                  Keys: l1_current, l2_current, l3_current (0.1A)
                        l1_voltage, l2_voltage, l3_voltage (0.1V)
                        active_power (0.1kW), reactive_power (Var)
                        power_factor (0.001), frequency (0.1Hz)
            simulation: True if this is simulation data
        """
        if inverter_id not in self._inverter_data:
            self.logger.warning(f"[DER-AVM] Invalid inverter_id: {inverter_id}")
            return
        
        with self._lock:
            self._inverter_data[inverter_id].update(data)
            self._inverter_data[inverter_id]['simulation'] = simulation
            # Update legacy _realtime_data for inverter 1
            if inverter_id == 1:
                self._realtime_data.update(data)
    
    def get_inverter_data(self, inverter_id: int) -> Dict:
        """Get data for specific inverter"""
        return self._inverter_data.get(inverter_id, {})
    
    def update_inverter_status(self, all_ok: bool, inverter_id: int = None):
        """Update inverter status for status flag"""
        if inverter_id and inverter_id in self._status_flags:
            self._status_flags[inverter_id].update_inverter_status(all_ok)
        else:
            # Update all
            for sf in self._status_flags.values():
                sf.update_inverter_status(all_ok)
            self.status_flag.update_inverter_status(all_ok)
    
    def check_communication(self) -> bool:
        """
        Check DER-AVM communication status
        
        Returns:
            True if communication OK or in Self-Control mode, False if timeout
        
        Note:
            In Self-Control mode (Action Mode = 0), communication timeout is not checked
            because DER-AVM Master does not poll data in this mode.
        """
        # Check if any inverter is in Self-Control mode
        is_self_control = self._is_self_control_mode()
        if is_self_control:
            # In Self-Control mode, no communication check needed
            if self._comm_error:
                self._comm_error = False
                self.logger.info("[DER-AVM] Self-Control mode - communication check disabled")
            return True
        
        if self._last_read_time == 0:
            return True  # Not started yet
        
        elapsed = time.time() - self._last_read_time
        
        if elapsed > DER_AVM_COMM_TIMEOUT:
            if not self._comm_error:
                self._comm_error = True
                self.logger.error(
                    f"[DER-AVM] Communication timeout! "
                    f"No read request for {elapsed:.1f}s (limit: {DER_AVM_COMM_TIMEOUT}s)"
                )
            return False
        else:
            if self._comm_error:
                self._comm_error = False
                self.logger.info("[DER-AVM] Communication restored")
            return True
    
    def _is_self_control_mode(self) -> bool:
        """
        Check if inverters are in Self-Control mode
        
        Returns:
            True if Action Mode is Self-Control (0) for inverter 1
        """
        # Check inverter 1's action mode (primary reference)
        inv_params = self._control_params.get(1, {})
        action_mode = inv_params.get(DerAvmRegisters.ACTION_MODE, DEFAULT_ACTION_MODE)
        return action_mode == DerAvmActionMode.SELF_CONTROL
    
    @property
    def is_comm_error(self) -> bool:
        """Check if communication error state"""
        return self._comm_error
    
    def get_statistics(self) -> Dict:
        """Get communication statistics"""
        return {
            'read_count': self._read_count,
            'write_count': self._write_count,
            'error_count': self._error_count,
            'comm_error': self._comm_error,
            'last_read_ago': time.time() - self._last_read_time if self._last_read_time else 0,
            'status_flag': self.status_flag.get_status_string(),
        }
    
    def _process_request(self, frame: bytes, inverter_id: int = 1):
        """Process received Modbus request for specific inverter"""
        slave_id = frame[0]
        fc = frame[1]
        
        # Store current inverter_id for use in handlers
        self._current_inverter_id = inverter_id
        
        fc_names = {0x03: 'READ_HOLDING', 0x06: 'WRITE_SINGLE', 0x10: 'WRITE_MULTIPLE'}
        fc_name = fc_names.get(fc, f'0x{fc:02X}')
        self.logger.info(f"[DER-AVM] RX INV{inverter_id} Frame: {frame.hex().upper()} (FC={fc_name})")
        
        if fc == self.FC_READ_HOLDING:
            self._handle_read_holding(frame, inverter_id)
        elif fc == self.FC_WRITE_SINGLE:
            self._handle_write_single(frame, inverter_id)
        elif fc == self.FC_WRITE_MULTIPLE:
            self._handle_write_multiple(frame, inverter_id)
        else:
            self._send_exception(fc, self.EX_ILLEGAL_FUNCTION, slave_id)
    
    def _handle_read_holding(self, frame: bytes, inverter_id: int = 1):
        """Handle read holding registers (FC03) for specific inverter"""
        slave_id = frame[0]
        addr = struct.unpack('>H', frame[2:4])[0]
        count = struct.unpack('>H', frame[4:6])[0]
        
        self.logger.info(f"[DER-AVM] INV{inverter_id} Read request: addr=0x{addr:04X}, count={count}")
        
        # Update last read time for communication monitoring
        self._last_read_time = time.time()
        self._read_count += 1
        
        # Check if reading real-time data area
        if addr >= DerAvmRegisters.REALTIME_START and addr + count <= DerAvmRegisters.REALTIME_END + 2:
            response_data = self._get_realtime_registers(addr, count, inverter_id)
        # Check if reading control parameters
        elif addr >= DerAvmRegisters.CONTROL_START_1 and addr + count <= DerAvmRegisters.CONTROL_END_1 + 1:
            response_data = self._get_control_registers(addr, count, inverter_id)
        elif addr == DerAvmRegisters.INVERTER_OFF:
            response_data = self._get_control_registers(addr, count, inverter_id)
        else:
            self._send_exception(self.FC_READ_HOLDING, self.EX_ILLEGAL_ADDRESS, slave_id)
            return
        
        if response_data is None:
            self._send_exception(self.FC_READ_HOLDING, self.EX_ILLEGAL_ADDRESS, slave_id)
            return
        
        # Build response with correct slave_id
        response = bytes([slave_id, self.FC_READ_HOLDING, len(response_data)]) + response_data
        response = ModbusCRC.append(response)
        
        self._send_response(response)
    
    def _handle_write_single(self, frame: bytes, inverter_id: int = 1):
        """Handle write single register (FC06) for specific inverter"""
        slave_id = frame[0]
        addr = struct.unpack('>H', frame[2:4])[0]
        value = struct.unpack('>H', frame[4:6])[0]
        
        # Convert to signed if needed
        if addr in [DerAvmRegisters.POWER_FACTOR_SET, DerAvmRegisters.REACTIVE_POWER_PCT]:
            if value > 32767:
                value = value - 65536
        
        self.logger.info(f"[DER-AVM] INV{inverter_id} Write single: addr=0x{addr:04X}, value={value}")
        self._write_count += 1
        
        # Validate and process for specific inverter
        if not self._validate_and_write(addr, value, inverter_id):
            self._send_exception(self.FC_WRITE_SINGLE, self.EX_ILLEGAL_VALUE, slave_id)
            return
        
        # Echo request as response (standard Modbus behavior)
        self._send_response(frame)
    
    def _handle_write_multiple(self, frame: bytes, inverter_id: int = 1):
        """Handle write multiple registers (FC16) for specific inverter"""
        slave_id = frame[0]
        addr = struct.unpack('>H', frame[2:4])[0]
        count = struct.unpack('>H', frame[4:6])[0]
        byte_count = frame[6]
        data = frame[7:7+byte_count]
        
        self.logger.info(f"[DER-AVM] INV{inverter_id} Write multiple: addr=0x{addr:04X}, count={count}")
        self._write_count += 1
        
        # Process each register
        for i in range(count):
            reg_addr = addr + i
            value = struct.unpack('>H', data[i*2:i*2+2])[0]
            
            # Convert to signed if needed
            if reg_addr in [DerAvmRegisters.POWER_FACTOR_SET, DerAvmRegisters.REACTIVE_POWER_PCT]:
                if value > 32767:
                    value = value - 65536
            
            if not self._validate_and_write(reg_addr, value, inverter_id):
                self._send_exception(self.FC_WRITE_MULTIPLE, self.EX_ILLEGAL_VALUE, slave_id)
                return
        
        # Build response with correct slave_id
        response = bytes([slave_id, self.FC_WRITE_MULTIPLE]) + frame[2:6]
        response = ModbusCRC.append(response)
        
        self._send_response(response)
    
    def _validate_and_write(self, addr: int, value: int, inverter_id: int = 1) -> bool:
        """Validate value and write to register, trigger control callback for specific inverter"""
        
        # Get status flag for this inverter
        status_flag = self._status_flags.get(inverter_id, self.status_flag)
        
        # Validate based on register
        if addr == DerAvmRegisters.POWER_FACTOR_SET:
            # Range: [-1000, -800] or [800, 1000]
            if not ((-1000 <= value <= -800) or (800 <= value <= 1000)):
                self.logger.warning(f"[DER-AVM] INV{inverter_id} Invalid PF value: {value}")
                return False
            ctrl_type = 'power_factor'
            
        elif addr == DerAvmRegisters.ACTION_MODE:
            # Values: 0, 2, 5
            if value not in [0, 2, 5]:
                self.logger.warning(f"[DER-AVM] INV{inverter_id} Invalid action mode: {value}")
                return False
            ctrl_type = 'action_mode'
            # Update status flag run state
            status_flag.set_run_state(value == DerAvmActionMode.SELF_CONTROL)
            
        elif addr == DerAvmRegisters.REACTIVE_POWER_PCT:
            # Range: [-484, 484]
            if not (-484 <= value <= 484):
                self.logger.warning(f"[DER-AVM] INV{inverter_id} Invalid reactive power %: {value}")
                return False
            ctrl_type = 'reactive_power'
            
        elif addr == DerAvmRegisters.ACTIVE_POWER_PCT:
            # Range: [0, 1100]
            if not (0 <= value <= 1100):
                self.logger.warning(f"[DER-AVM] INV{inverter_id} Invalid active power %: {value}")
                return False
            ctrl_type = 'active_power'
            # Set ACK bit
            status_flag.set_active_power_ack()
            
        elif addr == DerAvmRegisters.INVERTER_OFF:
            # Values: 0 (On), 1 (Off)
            if value not in [0, 1]:
                self.logger.warning(f"[DER-AVM] INV{inverter_id} Invalid inverter off value: {value}")
                return False
            ctrl_type = 'inverter_off'
            # Set ACK bit
            status_flag.set_inverter_action_ack()
            
        else:
            self.logger.warning(f"[DER-AVM] INV{inverter_id} Unknown register: 0x{addr:04X}")
            return False
        
        # Store value for this inverter
        with self._lock:
            if inverter_id in self._control_params:
                self._control_params[inverter_id][addr] = value
        
        self.logger.info(f"[DER-AVM] INV{inverter_id} Control: {ctrl_type}={value}")
        
        # Trigger callback to forward to specific inverter
        if self.control_callback:
            try:
                success = self.control_callback(inverter_id, ctrl_type, value)
                if not success:
                    self.logger.warning(f"[DER-AVM] INV{inverter_id} Control callback failed: {ctrl_type}")
            except Exception as e:
                self.logger.error(f"[DER-AVM] INV{inverter_id} Control callback error: {e}")
        
        return True
        
        return True
    
    def _get_realtime_registers(self, start_addr: int, count: int, inverter_id: int = 1) -> Optional[bytes]:
        """Get real-time data registers as bytes for specific inverter"""
        data = bytearray()
        
        # Get data for this inverter
        inv_data = self._inverter_data.get(inverter_id, {})
        status_flag = self._status_flags.get(inverter_id, self.status_flag)
        
        with self._lock:
            for i in range(0, count, 2):  # S32 = 2 registers
                addr = start_addr + i
                
                if addr == DerAvmRegisters.L1_CURRENT:
                    value = inv_data.get('l1_current', 0)
                elif addr == DerAvmRegisters.L2_CURRENT:
                    value = inv_data.get('l2_current', 0)
                elif addr == DerAvmRegisters.L3_CURRENT:
                    value = inv_data.get('l3_current', 0)
                elif addr == DerAvmRegisters.L1_VOLTAGE:
                    value = inv_data.get('l1_voltage', 0)
                elif addr == DerAvmRegisters.L2_VOLTAGE:
                    value = inv_data.get('l2_voltage', 0)
                elif addr == DerAvmRegisters.L3_VOLTAGE:
                    value = inv_data.get('l3_voltage', 0)
                elif addr == DerAvmRegisters.ACTIVE_POWER:
                    value = inv_data.get('active_power', 0)
                elif addr == DerAvmRegisters.REACTIVE_POWER:
                    value = inv_data.get('reactive_power', 0)
                elif addr == DerAvmRegisters.POWER_FACTOR:
                    value = inv_data.get('power_factor', 1000)
                elif addr == DerAvmRegisters.FREQUENCY:
                    value = inv_data.get('frequency', 600)
                elif addr == DerAvmRegisters.STATUS_FLAG:
                    # Read and clear ACK bits for this inverter
                    value = status_flag.read_and_clear_acks()
                else:
                    return None
                
                # Pack as S32 big-endian (high word first)
                data.extend(struct.pack('>i', value))
        
        return bytes(data)
    
    def _get_control_registers(self, start_addr: int, count: int, inverter_id: int = 1) -> Optional[bytes]:
        """Get control parameter registers as bytes for specific inverter"""
        data = bytearray()
        
        # Get control params for this inverter
        inv_params = self._control_params.get(inverter_id, {})
        
        with self._lock:
            for i in range(count):
                addr = start_addr + i
                
                if addr in inv_params:
                    value = inv_params[addr]
                    # Pack as U16/S16 big-endian
                    if value < 0:
                        value = value + 65536  # Convert to unsigned
                    data.extend(struct.pack('>H', value))
                else:
                    return None
        
        return bytes(data)
    
    def _send_exception(self, fc: int, exception_code: int, slave_id: int = None):
        """Send Modbus exception response"""
        if slave_id is None:
            slave_id = self.slave_id
        response = bytes([slave_id, fc | 0x80, exception_code])
        response = ModbusCRC.append(response)
        self._send_response(response)
        self._error_count += 1
    
    def _send_response(self, response: bytes):
        """Send Modbus response"""
        self.logger.info(f"[DER-AVM] TX: {response.hex().upper()}")

        if self.simulation_mode:
            return

        try:
            if self.use_cm4 and self._serial_ch:
                # CM4: pyserial direct write (full-auto direction control)
                self._serial_ch.write(response)
            else:
                # HAT: Convert to string for Waveshare library
                self.rs485.RS485_CH2_Write(''.join(chr(b) for b in response))
        except Exception as e:
            self.logger.error(f"[DER-AVM] Send error: {e}")
    
    def get_control_param(self, param: str, inverter_id: int = 1) -> int:
        """Get current control parameter value for specific inverter"""
        inv_params = self._control_params.get(inverter_id, {})
        with self._lock:
            if param == 'power_factor':
                return inv_params.get(DerAvmRegisters.POWER_FACTOR_SET, DEFAULT_POWER_FACTOR)
            elif param == 'action_mode':
                return inv_params.get(DerAvmRegisters.ACTION_MODE, DEFAULT_ACTION_MODE)
            elif param == 'reactive_power':
                return inv_params.get(DerAvmRegisters.REACTIVE_POWER_PCT, DEFAULT_REACTIVE_POWER_PCT)
            elif param == 'active_power':
                return self._control_params.get(DerAvmRegisters.ACTIVE_POWER_PCT, DEFAULT_ACTIVE_POWER_PCT)
            elif param == 'inverter_off':
                return self._control_params.get(DerAvmRegisters.INVERTER_OFF, DEFAULT_INVERTER_OFF)
        return 0


class DerAvmSlaveSimulation(DerAvmSlave):
    """Simulation mode DER-AVM Slave (no hardware)"""
    
    def __init__(self, slave_id: int = 1, control_callback: Callable = None):
        super().__init__(slave_id=slave_id, control_callback=control_callback, simulation_mode=True)
    
    def start(self) -> bool:
        self.logger.info("[DER-AVM-SIM] Simulation mode started")
        self._last_read_time = time.time()
        self.running = True
        return True
    
    def simulate_read_request(self):
        """Simulate DER-AVM read request (for testing)"""
        self._last_read_time = time.time()
        self._read_count += 1
        self.logger.debug("[DER-AVM-SIM] Simulated read request")
    
    def simulate_control(self, ctrl_type: str, value: int) -> bool:
        """Simulate DER-AVM control command (for testing)"""
        addr_map = {
            'power_factor': DerAvmRegisters.POWER_FACTOR_SET,
            'action_mode': DerAvmRegisters.ACTION_MODE,
            'reactive_power': DerAvmRegisters.REACTIVE_POWER_PCT,
            'active_power': DerAvmRegisters.ACTIVE_POWER_PCT,
            'inverter_off': DerAvmRegisters.INVERTER_OFF,
        }
        
        if ctrl_type not in addr_map:
            return False
        
        return self._validate_and_write(addr_map[ctrl_type], value)
