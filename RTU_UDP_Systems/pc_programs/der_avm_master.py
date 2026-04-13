#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DER-AVM Master Test Program for PC
Version: 1.0.7

Version 1.0.1:
- Version sync with RTU system V1.0.1

Version 1.0.0 - Unified Release:
- All components unified to V1.0.0
- Connects to RTU via USB-RS485 adapter to simulate DER-AVM Master
- Reads real-time data from RTU (registers 0x03E8~0x03FD)
- Sends control commands to RTU (registers 0x07D0~0x07D3, 0x0834)
- Monitors communication status
- Supports multiple inverter IDs cycling (1,2,3,...)
- Auto-stops when inverter enters Self-Control mode (Bit3=1)

Usage:
    python der_avm_master.py                     # Interactive port selection
    python der_avm_master.py --port COM3 --slave 1
    python der_avm_master.py --port /dev/ttyUSB0 --slaves 1,2,3
"""

import serial
import serial.tools.list_ports
import struct
import time
import argparse
import threading
import sys
from datetime import datetime


def list_com_ports():
    """List available COM ports with descriptions"""
    ports = serial.tools.list_ports.comports()
    return sorted(ports, key=lambda x: x.device)


def select_com_port():
    """Interactive COM port selection"""
    print(f"\n{'='*60}")
    print("  Available COM Ports")
    print(f"{'='*60}")
    
    ports = list_com_ports()
    
    if not ports:
        print("  [ERROR] No COM ports found!")
        print("  Please check USB-RS485 adapter connection.")
        return None
    
    for i, port in enumerate(ports, 1):
        desc = port.description if port.description else "Unknown"
        hwid = port.hwid if port.hwid else ""
        print(f"  {i}. {port.device}")
        print(f"     Description: {desc}")
        if hwid and hwid != "n/a":
            print(f"     Hardware ID: {hwid}")
        print()
    
    print(f"{'='*60}")
    
    while True:
        try:
            choice = input(f"  Select port [1-{len(ports)}] or enter port name: ").strip()
            
            if not choice:
                continue
            
            # Check if it's a number
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(ports):
                    return ports[idx].device
                else:
                    print(f"  [ERROR] Please enter 1-{len(ports)}")
            else:
                # Direct port name input (e.g., COM3, /dev/ttyUSB0)
                return choice.upper() if choice.upper().startswith('COM') else choice
                
        except (EOFError, KeyboardInterrupt):
            return None


class ModbusCRC:
    """Modbus CRC-16 calculation"""
    
    @staticmethod
    def calculate(data: bytes) -> int:
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
        crc = ModbusCRC.calculate(data)
        return data + struct.pack('<H', crc)
    
    @staticmethod
    def verify(data: bytes) -> bool:
        if len(data) < 4:
            return False
        received_crc = struct.unpack('<H', data[-2:])[0]
        calculated_crc = ModbusCRC.calculate(data[:-2])
        return received_crc == calculated_crc


class DerAvmMaster:
    """DER-AVM Master for testing RTU communication"""
    
    # Register addresses
    REG_REALTIME_START = 0x03E8
    REG_REALTIME_COUNT = 22  # 11 x S32 = 22 registers
    
    REG_POWER_FACTOR = 0x07D0
    REG_ACTION_MODE = 0x07D1
    REG_REACTIVE_POWER = 0x07D2
    REG_ACTIVE_POWER = 0x07D3
    REG_INVERTER_OFF = 0x0834
    
    def __init__(self, port: str, baudrate: int = 9600, slave_id: int = 1, slave_ids: list = None):
        self.port = port
        self.baudrate = baudrate
        self.slave_id = slave_id
        # Support multiple slave IDs for cycling
        self.slave_ids = slave_ids if slave_ids else [slave_id]
        self.current_slave_index = 0
        self.serial = None
        self.connected = False
        self.running = False
        
        # Statistics
        self.read_count = 0
        self.write_count = 0
        self.error_count = 0
        self.last_read_time = 0
        
        # Last read data (per slave)
        self.last_data = {}
        self.last_data_per_slave = {}
    
    def connect(self) -> bool:
        """Connect to serial port"""
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1.0
            )
            self.connected = True
            print(f"[OK] Connected to {self.port} @ {self.baudrate}bps")
            return True
        except Exception as e:
            print(f"[ERROR] Connection failed: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from serial port"""
        if self.serial:
            try:
                self.serial.close()
            except Exception:
                pass
        self.serial = None
        self.connected = False
        print("[OK] Disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()
    
    def _send_broadcast(self, register: int, value: int) -> bool:
        """Send Modbus broadcast write (slave_id=0, no response expected).
        All inverters on the bus execute the command but do not reply."""
        if not self.connected:
            return False

        request = struct.pack('>BBHH', 0x00, 0x06, register, value)
        request = ModbusCRC.append(request)

        self.serial.reset_input_buffer()
        self.serial.write(request)
        print(f"  TX (broadcast): {request.hex().upper()}")
        # No response expected for broadcast
        time.sleep(0.1)  # inter-frame gap
        self.write_count += 1
        print("  [OK] Broadcast sent (no response expected)")
        return True

    def _send_request(self, request: bytes) -> bytes:
        """Send Modbus request and receive response"""
        if not self.connected:
            return None

        # Broadcast: slave_id=0 → no response expected
        if request[0] == 0x00:
            self.serial.reset_input_buffer()
            self.serial.write(request)
            print(f"  TX (broadcast): {request.hex().upper()}")
            time.sleep(0.1)
            print("  [OK] Broadcast sent (no response expected)")
            self.write_count += 1
            return b'\x00'  # dummy response to indicate success

        # Clear input buffer
        self.serial.reset_input_buffer()

        # Send request
        self.serial.write(request)
        print(f"  TX: {request.hex().upper()}")

        # Wait for response start
        time.sleep(0.05)

        # Read response with proper framing: wait until no more bytes arriving
        response = b''
        while True:
            chunk = self.serial.read(self.serial.in_waiting or 1)
            if not chunk:
                break
            response += chunk
            time.sleep(0.05)  # inter-frame gap for slow devices

        if response:
            print(f"  RX: {response.hex().upper()}")

            # Verify CRC
            if not ModbusCRC.verify(response):
                print(f"  [ERROR] CRC verification failed (rx: {response[-2:].hex()}, data: {response.hex()})")
                self.error_count += 1
                return None
            
            # Check for exception response
            if response[1] & 0x80:
                exc_code = response[2]
                print(f"  [ERROR] Modbus exception: {exc_code}")
                self.error_count += 1
                return None
            
            return response
        else:
            print("  [ERROR] No response (timeout)")
            self.error_count += 1
            return None
    
    def read_realtime_data(self) -> dict:
        """Read real-time data from RTU (0x03E8~0x03FD)"""
        print(f"\n{'='*60}")
        print(f"  Reading Real-time Data (0x{self.REG_REALTIME_START:04X}, count={self.REG_REALTIME_COUNT})")
        print(f"{'='*60}")
        
        # Build request: FC03 Read Holding Registers
        request = struct.pack('>BBHH', 
            self.slave_id,
            0x03,  # Function code
            self.REG_REALTIME_START,
            self.REG_REALTIME_COUNT
        )
        request = ModbusCRC.append(request)
        
        response = self._send_request(request)
        
        if not response:
            return None
        
        self.read_count += 1
        self.last_read_time = time.time()
        
        # Parse response
        byte_count = response[2]
        data_bytes = response[3:3+byte_count]
        
        if len(data_bytes) < 44:
            print(f"  [ERROR] Insufficient data: {len(data_bytes)} bytes")
            return None
        
        # Parse S32 values (big-endian, high word first)
        def get_s32(offset):
            return struct.unpack('>i', data_bytes[offset:offset+4])[0]
        
        data = {
            'l1_current': get_s32(0),      # 0x03E8, scale 0.1A
            'l2_current': get_s32(4),      # 0x03EA, scale 0.1A
            'l3_current': get_s32(8),      # 0x03EC, scale 0.1A
            'l1_voltage': get_s32(12),     # 0x03EE, scale 0.1V
            'l2_voltage': get_s32(16),     # 0x03F0, scale 0.1V
            'l3_voltage': get_s32(20),     # 0x03F2, scale 0.1V
            'active_power': get_s32(24),   # 0x03F4, scale 0.1kW
            'reactive_power': get_s32(28), # 0x03F6, unit Var
            'power_factor': get_s32(32),   # 0x03F8, scale 0.001
            'frequency': get_s32(36),      # 0x03FA, scale 0.1Hz
            'status_flag': get_s32(40),    # 0x03FC, bitmap
        }
        
        self.last_data = data
        return data
    
    def read_control_params(self) -> dict:
        """Read control parameters from RTU"""
        print(f"\n{'='*60}")
        print(f"  Reading Control Parameters")
        print(f"{'='*60}")
        
        params = {}
        
        # Read 0x07D0~0x07D3 (4 registers)
        request = struct.pack('>BBHH', 
            self.slave_id, 0x03, 0x07D0, 4)
        request = ModbusCRC.append(request)
        
        response = self._send_request(request)
        if response and len(response) >= 11:
            data = response[3:-2]
            params['power_factor'] = struct.unpack('>h', data[0:2])[0]
            params['action_mode'] = struct.unpack('>H', data[2:4])[0]
            params['reactive_power'] = struct.unpack('>h', data[4:6])[0]
            params['active_power'] = struct.unpack('>H', data[6:8])[0]
        
        time.sleep(0.1)
        
        # Read 0x0834 (1 register)
        request = struct.pack('>BBHH', 
            self.slave_id, 0x03, 0x0834, 1)
        request = ModbusCRC.append(request)
        
        response = self._send_request(request)
        if response and len(response) >= 7:
            data = response[3:-2]
            params['inverter_off'] = struct.unpack('>H', data[0:2])[0]
        
        self.read_count += 1
        return params
    
    def write_power_factor(self, value: int, broadcast: bool = False) -> bool:
        """Write power factor setting (0x07D0)

        Args:
            value: Power factor * 1000, range [-1000,-800] or [800,1000]
            broadcast: If True, send to all inverters (slave_id=0, no response)
        """
        print(f"\n{'='*60}")
        print(f"  Writing Power Factor: {value} ({value/1000:.3f}){' [BROADCAST]' if broadcast else ''}")
        print(f"{'='*60}")

        # Validate
        if not ((-1000 <= value <= -800) or (800 <= value <= 1000)):
            print(f"  [ERROR] Invalid range. Must be [-1000,-800] or [800,1000]")
            return False

        # Convert to unsigned for transmission
        if value < 0:
            value = value + 65536

        if broadcast:
            return self._send_broadcast(self.REG_POWER_FACTOR, value)

        request = struct.pack('>BBHH',
            self.slave_id, 0x06, self.REG_POWER_FACTOR, value)
        request = ModbusCRC.append(request)

        response = self._send_request(request)
        if response:
            self.write_count += 1
            print("  [OK] Write successful")
            return True
        return False
    
    def write_action_mode(self, value: int, broadcast: bool = False) -> bool:
        """Write action mode (0x07D1)

        Args:
            value: 0=Self-control, 2=DER-AVM control, 5=Q(V) control
            broadcast: If True, send to all inverters (slave_id=0, no response)
        """
        print(f"\n{'='*60}")
        mode_names = {0: 'Self-control', 2: 'DER-AVM control', 5: 'Q(V) control'}
        print(f"  Writing Action Mode: {value} ({mode_names.get(value, 'Unknown')}){' [BROADCAST]' if broadcast else ''}")
        print(f"{'='*60}")

        if value not in [0, 2, 5]:
            print(f"  [ERROR] Invalid value. Must be 0, 2, or 5")
            return False

        if broadcast:
            return self._send_broadcast(self.REG_ACTION_MODE, value)

        request = struct.pack('>BBHH',
            self.slave_id, 0x06, self.REG_ACTION_MODE, value)
        request = ModbusCRC.append(request)

        response = self._send_request(request)
        if response:
            self.write_count += 1
            print("  [OK] Write successful")
            return True
        return False
    
    def write_reactive_power(self, value: int, broadcast: bool = False) -> bool:
        """Write reactive power percent (0x07D2)

        Args:
            value: Reactive power %, range [-484, 484], scale 0.1%
            broadcast: If True, send to all inverters (slave_id=0, no response)
        """
        print(f"\n{'='*60}")
        print(f"  Writing Reactive Power: {value} ({value/10:.1f}%){' [BROADCAST]' if broadcast else ''}")
        print(f"{'='*60}")

        if not (-484 <= value <= 484):
            print(f"  [ERROR] Invalid range. Must be [-484, 484]")
            return False

        if value < 0:
            value = value + 65536

        if broadcast:
            return self._send_broadcast(self.REG_REACTIVE_POWER, value)

        request = struct.pack('>BBHH',
            self.slave_id, 0x06, self.REG_REACTIVE_POWER, value)
        request = ModbusCRC.append(request)

        response = self._send_request(request)
        if response:
            self.write_count += 1
            print("  [OK] Write successful")
            return True
        return False
    
    def write_active_power(self, value: int, broadcast: bool = False) -> bool:
        """Write active power percent (0x07D3)

        Args:
            value: Active power %, range [0, 1100], scale 0.1%
            broadcast: If True, send to all inverters (slave_id=0, no response)
        """
        print(f"\n{'='*60}")
        print(f"  Writing Active Power: {value} ({value/10:.1f}%){' [BROADCAST]' if broadcast else ''}")
        print(f"{'='*60}")

        if not (0 <= value <= 1100):
            print(f"  [ERROR] Invalid range. Must be [0, 1100]")
            return False

        if broadcast:
            return self._send_broadcast(self.REG_ACTIVE_POWER, value)

        request = struct.pack('>BBHH',
            self.slave_id, 0x06, self.REG_ACTIVE_POWER, value)
        request = ModbusCRC.append(request)

        response = self._send_request(request)
        if response:
            self.write_count += 1
            print("  [OK] Write successful")
            return True
        return False
    
    def write_inverter_off(self, value: int, broadcast: bool = False) -> bool:
        """Write inverter on/off (0x0834)

        Args:
            value: 0=On, 1=Off
            broadcast: If True, send to all inverters (slave_id=0, no response)
        """
        print(f"\n{'='*60}")
        state = "OFF" if value == 1 else "ON"
        print(f"  Writing Inverter Control: {value} ({state}){' [BROADCAST]' if broadcast else ''}")
        print(f"{'='*60}")

        if value not in [0, 1]:
            print(f"  [ERROR] Invalid value. Must be 0 or 1")
            return False

        if broadcast:
            return self._send_broadcast(self.REG_INVERTER_OFF, value)

        request = struct.pack('>BBHH',
            self.slave_id, 0x06, self.REG_INVERTER_OFF, value)
        request = ModbusCRC.append(request)

        response = self._send_request(request)
        if response:
            self.write_count += 1
            print("  [OK] Write successful")
            return True
        return False
    
    def print_realtime_data(self, data: dict):
        """Print real-time data in formatted output"""
        if not data:
            print("  No data available")
            return
        
        print(f"\n{'='*60}")
        print(f"  Real-time Data @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [Slave ID: {self.slave_id}]")
        print(f"{'='*60}")
        print(f"  L1 Current (Ir) : {data['l1_current']/100:8.1f} A")
        print(f"  L2 Current (Is) : {data['l2_current']/100:8.1f} A")
        print(f"  L3 Current (It) : {data['l3_current']/100:8.1f} A")
        print(f"  L1 Voltage (Vr) : {data['l1_voltage']/10:8.1f} V")
        print(f"  L2 Voltage (Vs) : {data['l2_voltage']/10:8.1f} V")
        print(f"  L3 Voltage (Vt) : {data['l3_voltage']/10:8.1f} V")
        print(f"  Active Power    : {data['active_power']/10:8.1f} kW")
        print(f"  Reactive Power  : {data['reactive_power']:8d} Var")
        print(f"  Power Factor    : {data['power_factor']/1000:8.3f}")
        print(f"  Frequency       : {data['frequency']/10:8.1f} Hz")
        print(f"  Status Flag     : 0x{data['status_flag']:08X}")
        
        # Decode status flag
        sf = data['status_flag']
        print(f"\n  Status Flag Decode:")
        print(f"    Bit1 Inv Action    : {'FAIL' if sf & 0x01 else 'RUN'}")
        print(f"    Bit2 CB Action     : {'FAIL' if sf & 0x02 else 'RUN'}")
        print(f"    Bit3 Run State     : {'Self-Ctrl' if sf & 0x04 else 'DER-AVM'}")
        print(f"    Bit4 Active Pwr ACK: {'SET' if sf & 0x08 else 'CLR'}")
        print(f"    Bit5 Inv Action ACK: {'SET' if sf & 0x10 else 'CLR'}")
        print(f"{'='*60}")
    
    def print_control_params(self, params: dict):
        """Print control parameters"""
        if not params:
            print("  No parameters available")
            return
        
        print(f"\n{'='*60}")
        print(f"  Control Parameters")
        print(f"{'='*60}")
        
        pf = params.get('power_factor', 0)
        print(f"  Power Factor    : {pf} ({pf/1000:.3f})")
        
        mode = params.get('action_mode', 0)
        mode_names = {0: 'Self-control', 2: 'DER-AVM control', 5: 'Q(V) control'}
        print(f"  Action Mode     : {mode} ({mode_names.get(mode, 'Unknown')})")
        
        rp = params.get('reactive_power', 0)
        print(f"  Reactive Power  : {rp} ({rp/10:.1f}%)")
        
        ap = params.get('active_power', 0)
        print(f"  Active Power    : {ap} ({ap/10:.1f}%)")
        
        inv_off = params.get('inverter_off', 0)
        print(f"  Inverter Off    : {inv_off} ({'OFF' if inv_off else 'ON'})")
        print(f"{'='*60}")
    
    def print_statistics(self):
        """Print communication statistics"""
        print(f"\n{'='*60}")
        print(f"  Communication Statistics")
        print(f"{'='*60}")
        print(f"  Read Count  : {self.read_count}")
        print(f"  Write Count : {self.write_count}")
        print(f"  Error Count : {self.error_count}")
        if self.last_read_time:
            elapsed = time.time() - self.last_read_time
            print(f"  Last Read   : {elapsed:.1f}s ago")
        print(f"{'='*60}")
    
    def auto_read_loop(self, interval: int = 60):
        """Auto read loop (simulates DER-AVM 1-minute polling)
        
        Cycles through all configured slave IDs, reading one per interval.
        For example with slaves [1,2,3] and interval=60:
          - t=0: read slave 1
          - t=60: read slave 2
          - t=120: read slave 3
          - t=180: read slave 1 (repeat)
        
        Stops automatically when inverter enters Self-Control mode (Bit3=1).
        Returns True if stopped due to Self-Control mode (for interactive menu transition).
        """
        if len(self.slave_ids) > 1:
            print(f"\n[INFO] Starting auto-read loop (interval={interval}s)")
            print(f"[INFO] Cycling through Slave IDs: {self.slave_ids}")
        else:
            print(f"\n[INFO] Starting auto-read loop (interval={interval}s)")
        print("[INFO] Press Ctrl+C to stop")
        
        self.running = True
        self.current_slave_index = 0
        self_ctrl_detected = False
        
        while self.running:
            try:
                # Get current slave ID from cycle
                current_slave = self.slave_ids[self.current_slave_index]
                self.slave_id = current_slave
                
                data = self.read_realtime_data()
                if data:
                    self.last_data_per_slave[current_slave] = data
                    self.print_realtime_data(data)
                    
                    # Check Status Flag Bit3 - Self Control mode
                    if data['status_flag'] & 0x04:  # Bit3 = 1 = Self-Ctrl
                        print(f"\n{'='*60}")
                        print("  [INFO] Inverter entered Self-Control mode (Bit3=1)")
                        print("  [INFO] Stopping auto-read, returning to menu...")
                        print(f"{'='*60}")
                        self.running = False
                        self_ctrl_detected = True
                        break
                
                # Advance to next slave ID
                self.current_slave_index = (self.current_slave_index + 1) % len(self.slave_ids)
                
                # Wait for next interval
                for i in range(interval):
                    if not self.running:
                        break
                    time.sleep(1)
                    
            except KeyboardInterrupt:
                break
        
        self.running = False
        if not self_ctrl_detected:
            print("\n[INFO] Auto-read stopped")
        
        return self_ctrl_detected


def print_menu():
    """Print interactive menu"""
    print(f"\n{'='*60}")
    print("  DER-AVM Master Test Menu")
    print(f"{'='*60}")
    print("  1. Read Real-time Data (0x03E8~0x03FD)")
    print("  2. Read Control Parameters")
    print("  3. Write Power Factor (0x07D0)")
    print("  4. Write Action Mode (0x07D1)")
    print("  5. Write Reactive Power % (0x07D2)")
    print("  6. Write Active Power % (0x07D3)")
    print("  7. Write Inverter On/Off (0x0834)")
    print("  8. Auto Read (1 minute interval)")
    print("  9. Show Statistics")
    print("  0. Exit")
    print(f"{'='*60}")


def interactive_mode(master: DerAvmMaster):
    """Interactive command mode"""
    while True:
        print_menu()
        
        try:
            choice = input("  Select [0-9]: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        
        if choice == '0':
            break
        
        elif choice == '1':
            data = master.read_realtime_data()
            master.print_realtime_data(data)
        
        elif choice == '2':
            params = master.read_control_params()
            master.print_control_params(params)
        
        elif choice == '3':
            try:
                value = int(input("  Enter Power Factor * 1000 [-1000~-800 or 800~1000]: "))
                master.write_power_factor(value)
            except ValueError:
                print("  [ERROR] Invalid input")
        
        elif choice == '4':
            try:
                print("  0=Self-control, 2=DER-AVM control, 5=Q(V) control")
                value = int(input("  Enter Action Mode [0,2,5]: "))
                master.write_action_mode(value)
            except ValueError:
                print("  [ERROR] Invalid input")
        
        elif choice == '5':
            try:
                value = int(input("  Enter Reactive Power * 10 [-484~484]: "))
                master.write_reactive_power(value)
            except ValueError:
                print("  [ERROR] Invalid input")
        
        elif choice == '6':
            try:
                value = int(input("  Enter Active Power * 10 [0~1100] (1000=100%): "))
                master.write_active_power(value)
            except ValueError:
                print("  [ERROR] Invalid input")
        
        elif choice == '7':
            try:
                value = int(input("  Enter 0=ON, 1=OFF: "))
                master.write_inverter_off(value)
            except ValueError:
                print("  [ERROR] Invalid input")
        
        elif choice == '8':
            try:
                interval = int(input("  Enter interval in seconds [default 60]: ") or "60")
                master.auto_read_loop(interval)
            except ValueError:
                master.auto_read_loop(60)
        
        elif choice == '9':
            master.print_statistics()
        
        else:
            print("  [ERROR] Invalid selection")


def main():
    print(f"\n{'='*60}")
    print("  DER-AVM Master Test Program v1.0.1")
    print("  For RTU Communication Testing via USB-RS485")
    print(f"{'='*60}")
    
    parser = argparse.ArgumentParser(description='DER-AVM Master Test Program')
    parser.add_argument('--port', '-p', type=str, default=None,
                        help='Serial port (e.g., COM3, /dev/ttyUSB0)')
    parser.add_argument('--baudrate', '-b', type=int, default=9600,
                        help='Baudrate (default: 9600)')
    parser.add_argument('--slave', '-s', type=int, default=1,
                        help='RTU Slave ID (default: 1)')
    parser.add_argument('--slaves', type=str, default=None,
                        help='Multiple Slave IDs for cycling (e.g., "1,2,3")')
    parser.add_argument('--read', '-r', action='store_true',
                        help='Read real-time data once and exit')
    parser.add_argument('--auto', '-a', type=int, metavar='SEC',
                        help='Auto-read mode with interval in seconds')
    parser.add_argument('--list', '-l', action='store_true',
                        help='List available COM ports and exit')
    
    args = parser.parse_args()
    
    # List ports only
    if args.list:
        ports = list_com_ports()
        print(f"\n  Available COM Ports: {len(ports)}")
        print(f"  {'-'*50}")
        for port in ports:
            print(f"  {port.device}: {port.description}")
        return
    
    # Get port - interactive if not specified
    port = args.port
    if not port:
        port = select_com_port()
        if not port:
            print("\n  [ERROR] No port selected. Exiting.")
            sys.exit(1)
    
    # Parse slave IDs
    slave_ids = None
    if args.slaves:
        try:
            slave_ids = [int(x.strip()) for x in args.slaves.split(',')]
            for sid in slave_ids:
                if not (1 <= sid <= 247):
                    print(f"  [ERROR] Invalid slave ID {sid}. Must be 1-247.")
                    sys.exit(1)
        except ValueError:
            print("  [ERROR] Invalid --slaves format. Use comma-separated integers (e.g., 1,2,3)")
            sys.exit(1)
    
    # Get slave ID interactively if port was selected interactively
    slave_id = args.slave
    if not args.port and not slave_ids:
        try:
            slave_input = input(f"  Enter RTU Slave ID(s) [default: {slave_id}, or comma-separated e.g. 1,2,3]: ").strip()
            if slave_input:
                if ',' in slave_input:
                    slave_ids = [int(x.strip()) for x in slave_input.split(',')]
                    slave_id = slave_ids[0]
                else:
                    slave_id = int(slave_input)
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
    
    # Get baudrate interactively if port was selected interactively
    baudrate = args.baudrate
    if not args.port:
        try:
            baud_input = input(f"  Enter Baudrate [default: {baudrate}]: ").strip()
            if baud_input:
                baudrate = int(baud_input)
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
    
    # Create master
    master = DerAvmMaster(
        port=port,
        baudrate=baudrate,
        slave_id=slave_id,
        slave_ids=slave_ids
    )
    
    print(f"\n  Port     : {port}")
    print(f"  Baudrate : {baudrate}")
    if slave_ids and len(slave_ids) > 1:
        print(f"  Slave IDs: {slave_ids} (cycling)")
    else:
        print(f"  Slave ID : {slave_id}")
    
    # Connect
    if not master.connect():
        sys.exit(1)
    
    try:
        if args.read:
            # Single read mode
            data = master.read_realtime_data()
            master.print_realtime_data(data)
        
        elif args.auto:
            # Auto-read mode
            master.auto_read_loop(args.auto)
        
        else:
            # Interactive mode
            interactive_mode(master)
    
    except KeyboardInterrupt:
        print("\n\n[INFO] Interrupted by user")
    
    finally:
        master.print_statistics()
        master.disconnect()


if __name__ == '__main__':
    main()
