#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
Modbus RTU Master with Enhanced Diagnostics
Version: 1.0.6

Changes in 1.3.3:
- Added HAT transaction lock support for DER-AVM priority
- CH1 waits for CH2 (DER-AVM) to complete before transmitting
- Prevents SPI bus conflicts during concurrent channel access

Changes in 1.3.2:
- Enhanced _flush_input() to use channel.flush_rx()
- Increased POST_TX_DELAY to 20ms for stable communication
- Added PRE_TX_DELAY for RX buffer settling

Changes in 1.3.1:
- Added INTER_CHAR_TIMEOUT for proper frame end detection
- Fixed CRC errors caused by incomplete frame reception
- 9600 baud: 5ms inter-character timeout (approximately 5 byte times)

Changes in 1.3.0:
- Added Modbus exception code parsing and handling
- Added detailed communication logging with timestamps
- Added automatic retry with exponential backoff
- Added communication statistics tracking
- Added diagnostic information for field troubleshooting
- Support for FC 0x03, 0x04, 0x06, 0x10

Changes in 1.2.0:
- Basic Modbus RTU Master implementation
"""

import time
import sys
import os
import logging
from datetime import datetime
from collections import deque

# Add library path
libdir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(libdir)

from modbus_utils import add_crc, verify_crc, bytes_to_hex

# Import HAT transaction lock for DER-AVM priority
try:
    from rs485_channel import acquire_hat_lock, release_hat_lock
    HAT_LOCK_AVAILABLE = True
except ImportError:
    HAT_LOCK_AVAILABLE = False


# =============================================================================
# Modbus Exception Definitions
# =============================================================================

class ModbusException(Exception):
    """Modbus communication exception with detailed error information"""
    
    # Modbus standard exception codes
    EXCEPTION_CODES = {
        0x01: ("ILLEGAL_FUNCTION", "Function code not supported by slave"),
        0x02: ("ILLEGAL_DATA_ADDRESS", "Register address not valid"),
        0x03: ("ILLEGAL_DATA_VALUE", "Value out of range"),
        0x04: ("SLAVE_DEVICE_FAILURE", "Unrecoverable error in slave"),
        0x05: ("ACKNOWLEDGE", "Request accepted, processing (retry later)"),
        0x06: ("SLAVE_DEVICE_BUSY", "Slave busy, retry later"),
        0x08: ("MEMORY_PARITY_ERROR", "Memory parity error in slave"),
        0x0A: ("GATEWAY_PATH_UNAVAILABLE", "Gateway path not available"),
        0x0B: ("GATEWAY_TARGET_FAILED", "Gateway target device failed"),
    }
    
    # Custom error codes (0x80+)
    ERROR_TIMEOUT = 0x80
    ERROR_CRC = 0x81
    ERROR_NO_RESPONSE = 0x82
    ERROR_INVALID_RESPONSE = 0x83
    ERROR_FRAME_ERROR = 0x84
    
    CUSTOM_ERRORS = {
        0x80: ("TIMEOUT", "No response within timeout period"),
        0x81: ("CRC_ERROR", "Response CRC verification failed"),
        0x82: ("NO_RESPONSE", "No bytes received from slave"),
        0x83: ("INVALID_RESPONSE", "Response format invalid"),
        0x84: ("FRAME_ERROR", "Incomplete or corrupted frame"),
    }
    
    def __init__(self, code, slave_id=0, function=0, address=0, detail=""):
        self.code = code
        self.slave_id = slave_id
        self.function = function
        self.address = address
        self.detail = detail
        self.timestamp = datetime.now()
        
        # Get error name and description
        if code in self.EXCEPTION_CODES:
            self.name, self.description = self.EXCEPTION_CODES[code]
            self.is_modbus_exception = True
        elif code in self.CUSTOM_ERRORS:
            self.name, self.description = self.CUSTOM_ERRORS[code]
            self.is_modbus_exception = False
        else:
            self.name = f"UNKNOWN_0x{code:02X}"
            self.description = "Unknown error code"
            self.is_modbus_exception = False
        
        super().__init__(self._format_message())
    
    def update_context(self, slave_id, function, address):
        """Update context info and regenerate message"""
        self.slave_id = slave_id
        self.function = function
        self.address = address
        # Update the exception message
        self.args = (self._format_message(),)
    
    def _format_message(self):
        msg = f"[{self.name}] Slave={self.slave_id} FC=0x{self.function:02X} Addr=0x{self.address:04X}"
        if self.detail:
            msg += f" - {self.detail}"
        return msg
    
    @property
    def is_retryable(self):
        """Check if this error warrants a retry"""
        retryable_codes = [
            0x05, 0x06,  # Modbus: Acknowledge, Busy
            self.ERROR_TIMEOUT, self.ERROR_CRC, self.ERROR_NO_RESPONSE
        ]
        return self.code in retryable_codes


# =============================================================================
# Communication Statistics
# =============================================================================

class ModbusStats:
    """Track Modbus communication statistics for diagnostics"""
    
    def __init__(self, history_size=100):
        self.history_size = history_size
        self.reset()
    
    def reset(self):
        """Reset all statistics"""
        self.total_requests = 0
        self.successful = 0
        self.failed = 0
        self.timeouts = 0
        self.crc_errors = 0
        self.modbus_exceptions = 0
        self.retries = 0
        
        # Per-slave statistics
        self.slave_stats = {}
        
        # Recent transaction history (for debugging)
        self.history = deque(maxlen=self.history_size)
        
        # Timing statistics
        self.min_response_time = float('inf')
        self.max_response_time = 0
        self.total_response_time = 0
        
        # Error breakdown
        self.error_counts = {}
        
        # Session start time
        self.start_time = time.time()
    
    def record_success(self, slave_id, function, address, response_time):
        """Record successful transaction"""
        self.total_requests += 1
        self.successful += 1
        
        # Update timing
        self.total_response_time += response_time
        self.min_response_time = min(self.min_response_time, response_time)
        self.max_response_time = max(self.max_response_time, response_time)
        
        # Update slave stats
        if slave_id not in self.slave_stats:
            self.slave_stats[slave_id] = {'success': 0, 'fail': 0}
        self.slave_stats[slave_id]['success'] += 1
        
        # Add to history
        self.history.append({
            'time': datetime.now(),
            'slave': slave_id,
            'func': function,
            'addr': address,
            'result': 'OK',
            'response_ms': response_time * 1000
        })
    
    def record_failure(self, slave_id, function, address, error: ModbusException):
        """Record failed transaction"""
        self.total_requests += 1
        self.failed += 1
        
        # Update error type counts
        if error.code == ModbusException.ERROR_TIMEOUT:
            self.timeouts += 1
        elif error.code == ModbusException.ERROR_CRC:
            self.crc_errors += 1
        elif error.is_modbus_exception:
            self.modbus_exceptions += 1
        
        # Update error breakdown
        if error.name not in self.error_counts:
            self.error_counts[error.name] = 0
        self.error_counts[error.name] += 1
        
        # Update slave stats
        if slave_id not in self.slave_stats:
            self.slave_stats[slave_id] = {'success': 0, 'fail': 0}
        self.slave_stats[slave_id]['fail'] += 1
        
        # Add to history
        self.history.append({
            'time': datetime.now(),
            'slave': slave_id,
            'func': function,
            'addr': address,
            'result': 'FAIL',
            'error': error.name
        })
    
    def record_retry(self):
        """Record retry attempt"""
        self.retries += 1
    
    @property
    def success_rate(self):
        """Calculate success rate percentage"""
        if self.total_requests == 0:
            return 100.0
        return (self.successful / self.total_requests) * 100
    
    @property
    def avg_response_time(self):
        """Calculate average response time in ms"""
        if self.successful == 0:
            return 0
        return (self.total_response_time / self.successful) * 1000
    
    def get_summary(self):
        """Get statistics summary string"""
        uptime = time.time() - self.start_time
        lines = [
            "=" * 60,
            "MODBUS COMMUNICATION STATISTICS",
            "=" * 60,
            f"Uptime: {uptime:.1f}s",
            f"Total Requests: {self.total_requests}",
            f"Successful: {self.successful} ({self.success_rate:.1f}%)",
            f"Failed: {self.failed}",
            f"  - Timeouts: {self.timeouts}",
            f"  - CRC Errors: {self.crc_errors}",
            f"  - Modbus Exceptions: {self.modbus_exceptions}",
            f"Retries: {self.retries}",
            "",
            "Response Time:",
            f"  - Min: {self.min_response_time*1000:.1f}ms" if self.min_response_time != float('inf') else "  - Min: N/A",
            f"  - Max: {self.max_response_time*1000:.1f}ms",
            f"  - Avg: {self.avg_response_time:.1f}ms",
        ]
        
        if self.slave_stats:
            lines.append("")
            lines.append("Per-Slave Statistics:")
            for slave_id, stats in sorted(self.slave_stats.items()):
                total = stats['success'] + stats['fail']
                rate = (stats['success'] / total * 100) if total > 0 else 0
                lines.append(f"  Slave {slave_id}: {stats['success']}/{total} ({rate:.1f}%)")
        
        if self.error_counts:
            lines.append("")
            lines.append("Error Breakdown:")
            for error, count in sorted(self.error_counts.items(), key=lambda x: -x[1]):
                lines.append(f"  {error}: {count}")
        
        lines.append("=" * 60)
        return "\n".join(lines)
    
    def get_recent_errors(self, count=10):
        """Get recent error transactions"""
        errors = [h for h in self.history if h['result'] == 'FAIL']
        return list(errors)[-count:]


# =============================================================================
# Modbus Master with Enhanced Diagnostics
# =============================================================================

class ModbusMaster:
    """Modbus RTU Master with comprehensive error handling and diagnostics"""
    
    VERSION = "1.0.7"
    
    # Retry configuration
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_DELAY = 0.1  # 100ms base delay
    RETRY_BACKOFF = 2.0  # Exponential backoff factor
    MAX_RETRY_DELAY = 2.0  # Maximum retry delay
    
    # Frame timing defaults (for Pi 5 @ 9600 baud)
    INTER_FRAME_DELAY = 0.005  # 5ms between frames
    PRE_TX_DELAY = 0.010  # 10ms before transmit (let RX settle)
    POST_TX_DELAY = 0.020  # 20ms after transmit (wait for response start)
    INTER_CHAR_TIMEOUT = 0.005  # 5ms inter-character timeout (frame end detection)
    
    @staticmethod
    def _is_pi_zero():
        """Detect if running on Raspberry Pi Zero"""
        try:
            with open('/proc/device-tree/model', 'r') as f:
                model = f.read().lower()
            return 'zero' in model
        except:
            return False
    
    def __init__(self, rs485_channel, slave_address=0x01, timeout=1.0):
        """
        Initialize Modbus Master
        
        Args:
            rs485_channel: RS485 channel object
            slave_address: Default slave address
            timeout: Response timeout in seconds
        """
        self.channel = rs485_channel
        self.slave_address = slave_address
        self.timeout = timeout
        self.debug = False
        self.logger = logging.getLogger(f"ModbusMaster")
        
        # Adjust timing for Pi Zero (slower CPU)
        if self._is_pi_zero():
            self.PRE_TX_DELAY = 0.030      # 30ms (3x)
            self.POST_TX_DELAY = 0.050     # 50ms (2.5x)
            self.INTER_CHAR_TIMEOUT = 0.015  # 15ms (3x)
            self.INTER_FRAME_DELAY = 0.015   # 15ms (3x)
            self.logger.info("Pi Zero detected: Using extended timing delays")
        
        # Statistics
        self.stats = ModbusStats()
        
        # Retry settings
        self.max_retries = self.DEFAULT_MAX_RETRIES
        self.retry_delay = self.DEFAULT_RETRY_DELAY
        self.auto_retry = True
        
        # Last transaction info (for debugging)
        self.last_tx = None
        self.last_rx = None
        self.last_error = None
    
    def set_debug(self, enable):
        """Enable/disable debug output"""
        self.debug = enable
        if enable:
            self.logger.setLevel(logging.DEBUG)
    
    def set_retry_config(self, max_retries=3, base_delay=0.1, auto_retry=True):
        """Configure retry behavior"""
        self.max_retries = max_retries
        self.retry_delay = base_delay
        self.auto_retry = auto_retry
    
    def _log_frame(self, direction, frame, elapsed_ms=None):
        """Log frame with timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        hex_str = bytes_to_hex(frame) if frame else "(empty)"
        
        if direction == "TX":
            msg = f"[{timestamp}] TX >> {hex_str}"
            self.last_tx = frame
        else:
            elapsed_str = f" ({elapsed_ms:.1f}ms)" if elapsed_ms else ""
            msg = f"[{timestamp}] RX << {hex_str}{elapsed_str}"
            self.last_rx = frame
        
        if self.debug:
            print(f"  {msg}")
        self.logger.debug(msg)
    
    def _log_error(self, error: ModbusException):
        """Log error with details"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        msg = f"[{timestamp}] ERROR: {error}"
        
        if self.debug:
            print(f"  {msg}")
        self.logger.warning(msg)
        self.last_error = error
    
    def _send_request(self, request):
        """Send Modbus request frame"""
        # Clear any pending data
        self._flush_input()
        
        # Wait for bus to settle before transmit
        time.sleep(self.PRE_TX_DELAY)
        
        # Add CRC and send
        frame = add_crc(request)
        self._log_frame("TX", frame)
        
        self.channel.write(bytes(frame))
        time.sleep(self.POST_TX_DELAY)
        
        return frame
    
    def _flush_input(self):
        """Flush input buffer completely"""
        flushed = 0
        
        # Try to use channel's flush_rx() if available
        if hasattr(self.channel, 'flush_rx'):
            flushed = self.channel.flush_rx()
        else:
            # Fallback: read all available bytes
            while True:
                byte = self.channel.read_byte()
                if not byte:
                    break
                flushed += 1
        
        # Additional delay to ensure buffer is clear
        time.sleep(0.005)
        
        if flushed > 0 and self.debug:
            print(f"  [Flushed {flushed} bytes from input buffer]")
    
    def _receive_response(self, expected_function):
        """
        Receive Modbus response frame with detailed error detection
        
        Returns:
            tuple: (response_bytes, elapsed_time) or raises ModbusException
        """
        start_time = time.time()
        buffer = []
        first_byte_time = None
        last_byte_time = None
        
        while (time.time() - start_time) < self.timeout:
            byte = self.channel.read_byte()
            
            if byte:
                current_time = time.time()
                if first_byte_time is None:
                    first_byte_time = current_time
                last_byte_time = current_time
                
                buffer.append(ord(byte) if isinstance(byte, str) else byte)
                
                # Check for exception response (function code + 0x80)
                if len(buffer) >= 5 and buffer[1] == (expected_function | 0x80):
                    # Exception response: addr + func|0x80 + exception_code + CRC(2)
                    break
                
                # Check if we have complete normal response
                if len(buffer) >= 5:
                    expected_len = self._get_expected_length(buffer)
                    if expected_len and len(buffer) >= expected_len:
                        break
            else:
                # No byte received - check inter-character timeout
                if last_byte_time and len(buffer) >= 5:
                    idle_time = time.time() - last_byte_time
                    if idle_time > self.INTER_CHAR_TIMEOUT:
                        # Frame complete (no more bytes after timeout)
                        break
            
            time.sleep(0.001)
        
        elapsed = time.time() - start_time
        elapsed_ms = elapsed * 1000
        
        # Log received data
        self._log_frame("RX", buffer, elapsed_ms)
        
        # Analyze response
        if not buffer:
            raise ModbusException(
                ModbusException.ERROR_NO_RESPONSE,
                detail=f"No response after {elapsed_ms:.0f}ms"
            )
        
        if first_byte_time and (first_byte_time - start_time) > (self.timeout * 0.9):
            # Response started very late - possible timing issue
            self.logger.warning(f"Late response start: {(first_byte_time-start_time)*1000:.0f}ms")
        
        return buffer, elapsed
    
    def _get_expected_length(self, buffer):
        """Calculate expected response length based on function code"""
        if len(buffer) < 2:
            return None
        
        func = buffer[1]
        
        # Exception response
        if func & 0x80:
            return 5  # addr + func|0x80 + exception_code + CRC(2)
        
        if func in [0x03, 0x04]:  # Read Holding/Input Registers
            if len(buffer) >= 3:
                byte_count = buffer[2]
                return 3 + byte_count + 2  # addr + func + count + data + CRC
        elif func == 0x06:  # Write Single Register
            return 8  # addr + func + addr(2) + value(2) + CRC(2)
        elif func == 0x10:  # Write Multiple Registers
            return 8  # addr + func + addr(2) + quantity(2) + CRC(2)
        
        return None
    
    def _validate_response(self, response, slave_addr, function, start_address):
        """
        Validate response and extract data or raise exception
        
        Returns:
            Validated response data
        """
        # Check minimum length
        if len(response) < 5:
            raise ModbusException(
                ModbusException.ERROR_FRAME_ERROR,
                slave_addr, function, start_address,
                f"Response too short: {len(response)} bytes"
            )
        
        # Verify CRC
        if not verify_crc(response):
            raise ModbusException(
                ModbusException.ERROR_CRC,
                slave_addr, function, start_address,
                f"CRC mismatch in {len(response)}-byte response"
            )
        
        # Check for Modbus exception response
        if response[1] == (function | 0x80):
            exception_code = response[2]
            raise ModbusException(
                exception_code,
                slave_addr, function, start_address
            )
        
        # Verify slave address
        if response[0] != slave_addr:
            raise ModbusException(
                ModbusException.ERROR_INVALID_RESPONSE,
                slave_addr, function, start_address,
                f"Wrong slave addr: expected {slave_addr}, got {response[0]}"
            )
        
        # Verify function code
        if response[1] != function:
            raise ModbusException(
                ModbusException.ERROR_INVALID_RESPONSE,
                slave_addr, function, start_address,
                f"Wrong function: expected 0x{function:02X}, got 0x{response[1]:02X}"
            )
        
        return response
    
    def _execute_with_retry(self, func, slave_addr, function, start_address, *args):
        """Execute function with automatic retry on retryable errors"""
        last_exception = None
        
        # Get channel number for HAT lock
        channel_num = getattr(self.channel, 'channel', 1)
        
        for attempt in range(self.max_retries + 1):
            # Acquire HAT transaction lock (DER-AVM priority)
            lock_acquired = False
            if HAT_LOCK_AVAILABLE:
                lock_acquired = acquire_hat_lock(channel_num, timeout=5.0)
                if not lock_acquired:
                    self.logger.warning(f"HAT lock timeout for CH{channel_num}")
            
            try:
                return func(slave_addr, function, start_address, *args, _attempt=attempt)
            
            except ModbusException as e:
                last_exception = e
                self._log_error(e)
                self.stats.record_failure(slave_addr, function, start_address, e)
                
                # Check if we should retry
                if not self.auto_retry or not e.is_retryable:
                    raise
                
                if attempt < self.max_retries:
                    # Calculate delay with exponential backoff
                    delay = min(
                        self.retry_delay * (self.RETRY_BACKOFF ** attempt),
                        self.MAX_RETRY_DELAY
                    )
                    
                    self.stats.record_retry()
                    if self.debug:
                        print(f"  [Retry {attempt+1}/{self.max_retries} after {delay*1000:.0f}ms]")
                    self.logger.info(f"Retry {attempt+1}/{self.max_retries} for slave {slave_addr}")
                    
                    time.sleep(delay)
            
            finally:
                # Always release lock
                if HAT_LOCK_AVAILABLE and lock_acquired:
                    release_hat_lock(channel_num)
        
        # All retries exhausted
        raise last_exception
    
    # =========================================================================
    # Public API - Read Functions
    # =========================================================================
    
    def read_holding_registers(self, start_address, quantity, slave_addr=None):
        """
        Modbus Function 0x03: Read Holding Registers
        
        Args:
            start_address: Starting register address (0-65535)
            quantity: Number of registers to read (1-125)
            slave_addr: Slave address (optional, uses default if None)
        
        Returns:
            list: Register values, or None if error (for backward compatibility)
        """
        if slave_addr is None:
            slave_addr = self.slave_address
        
        try:
            if self.auto_retry:
                return self._execute_with_retry(
                    self._read_registers_impl,
                    slave_addr, 0x03, start_address, quantity
                )
            else:
                return self._read_registers_impl(slave_addr, 0x03, start_address, quantity)
        except ModbusException:
            return None  # Backward compatibility
    
    def read_input_registers(self, start_address, quantity, slave_addr=None):
        """
        Modbus Function 0x04: Read Input Registers
        
        Args:
            start_address: Starting register address (0-65535)
            quantity: Number of registers to read (1-125)
            slave_addr: Slave address (optional, uses default if None)
        
        Returns:
            list: Register values, or None if error
        """
        if slave_addr is None:
            slave_addr = self.slave_address
        
        try:
            if self.auto_retry:
                return self._execute_with_retry(
                    self._read_registers_impl,
                    slave_addr, 0x04, start_address, quantity
                )
            else:
                return self._read_registers_impl(slave_addr, 0x04, start_address, quantity)
        except ModbusException:
            return None
    
    def _read_registers_impl(self, slave_addr, function, start_address, quantity, _attempt=0):
        """Implementation of register read"""
        # Build request
        request = [
            slave_addr,
            function,
            (start_address >> 8) & 0xFF,
            start_address & 0xFF,
            (quantity >> 8) & 0xFF,
            quantity & 0xFF
        ]
        
        # Send and receive
        start_time = time.time()
        self._send_request(request)
        
        try:
            response, elapsed = self._receive_response(function)
        except ModbusException as e:
            e.update_context(slave_addr, function, start_address)
            raise
        
        # Validate
        self._validate_response(response, slave_addr, function, start_address)
        
        # Extract register data
        byte_count = response[2]
        expected_bytes = quantity * 2
        
        if byte_count != expected_bytes:
            raise ModbusException(
                ModbusException.ERROR_INVALID_RESPONSE,
                slave_addr, function, start_address,
                f"Byte count mismatch: expected {expected_bytes}, got {byte_count}"
            )
        
        data = response[3:3+byte_count]
        registers = []
        for i in range(0, byte_count, 2):
            value = (data[i] << 8) | data[i+1]
            registers.append(value)
        
        # Record success
        self.stats.record_success(slave_addr, function, start_address, elapsed)
        
        return registers
    
    # =========================================================================
    # Public API - Write Functions
    # =========================================================================
    
    def write_single_register(self, register_address, value, slave_addr=None):
        """
        Modbus Function 0x06: Write Single Register
        
        Args:
            register_address: Register address (0-65535)
            value: Register value (0-65535)
            slave_addr: Slave address (optional)
        
        Returns:
            bool: True if successful, False otherwise
        """
        if slave_addr is None:
            slave_addr = self.slave_address
        
        try:
            if self.auto_retry:
                return self._execute_with_retry(
                    self._write_single_impl,
                    slave_addr, 0x06, register_address, value
                )
            else:
                return self._write_single_impl(slave_addr, 0x06, register_address, value)
        except ModbusException:
            return False
    
    def _write_single_impl(self, slave_addr, function, register_address, value, _attempt=0):
        """Implementation of single register write"""
        # Build request
        request = [
            slave_addr,
            function,
            (register_address >> 8) & 0xFF,
            register_address & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF
        ]
        
        # Send and receive
        start_time = time.time()
        self._send_request(request)
        
        try:
            response, elapsed = self._receive_response(function)
        except ModbusException as e:
            e.update_context(slave_addr, function, start_address)
            raise
        
        # Validate
        self._validate_response(response, slave_addr, function, register_address)
        
        # Verify echo (response should match request for function 0x06)
        request_with_crc = add_crc(request)
        if response != request_with_crc:
            raise ModbusException(
                ModbusException.ERROR_INVALID_RESPONSE,
                slave_addr, function, register_address,
                "Response does not echo request"
            )
        
        # Record success
        self.stats.record_success(slave_addr, function, register_address, elapsed)
        
        return True
    
    def write_multiple_registers(self, start_address, values, slave_addr=None):
        """
        Modbus Function 0x10: Write Multiple Registers
        
        Args:
            start_address: Starting register address (0-65535)
            values: List of register values
            slave_addr: Slave address (optional)
        
        Returns:
            bool: True if successful, False otherwise
        """
        if slave_addr is None:
            slave_addr = self.slave_address
        
        try:
            if self.auto_retry:
                return self._execute_with_retry(
                    self._write_multiple_impl,
                    slave_addr, 0x10, start_address, values
                )
            else:
                return self._write_multiple_impl(slave_addr, 0x10, start_address, values)
        except ModbusException:
            return False
    
    def _write_multiple_impl(self, slave_addr, function, start_address, values, _attempt=0):
        """Implementation of multiple register write"""
        quantity = len(values)
        byte_count = quantity * 2
        
        # Build request
        request = [
            slave_addr,
            function,
            (start_address >> 8) & 0xFF,
            start_address & 0xFF,
            (quantity >> 8) & 0xFF,
            quantity & 0xFF,
            byte_count
        ]
        
        # Add register values
        for value in values:
            request.append((value >> 8) & 0xFF)
            request.append(value & 0xFF)
        
        # Send and receive
        start_time = time.time()
        self._send_request(request)
        
        try:
            response, elapsed = self._receive_response(function)
        except ModbusException as e:
            e.update_context(slave_addr, function, start_address)
            raise
        
        # Validate
        self._validate_response(response, slave_addr, function, start_address)
        
        # Verify response format
        if len(response) != 8:
            raise ModbusException(
                ModbusException.ERROR_INVALID_RESPONSE,
                slave_addr, function, start_address,
                f"Invalid response length: {len(response)}"
            )
        
        # Verify echoed address and quantity
        resp_addr = (response[2] << 8) | response[3]
        resp_qty = (response[4] << 8) | response[5]
        
        if resp_addr != start_address or resp_qty != quantity:
            raise ModbusException(
                ModbusException.ERROR_INVALID_RESPONSE,
                slave_addr, function, start_address,
                f"Addr/Qty mismatch: {resp_addr}/{resp_qty} vs {start_address}/{quantity}"
            )
        
        # Record success
        self.stats.record_success(slave_addr, function, start_address, elapsed)
        
        return True
    
    # =========================================================================
    # Diagnostic Functions
    # =========================================================================
    
    def get_stats(self):
        """Get communication statistics object"""
        return self.stats
    
    def print_stats(self):
        """Print communication statistics summary"""
        print(self.stats.get_summary())
    
    def get_last_transaction(self):
        """Get details of last transaction for debugging"""
        return {
            'tx': bytes_to_hex(self.last_tx) if self.last_tx else None,
            'rx': bytes_to_hex(self.last_rx) if self.last_rx else None,
            'error': str(self.last_error) if self.last_error else None
        }
    
    def diagnose_connection(self, slave_addr=None):
        """
        Run diagnostic tests on connection
        
        Returns:
            dict: Diagnostic results
        """
        if slave_addr is None:
            slave_addr = self.slave_address
        
        results = {
            'slave_addr': slave_addr,
            'tests': [],
            'recommendations': []
        }
        
        # Test 1: Basic connectivity (read register 0)
        old_auto_retry = self.auto_retry
        try:
            self.auto_retry = False
            self.read_holding_registers(0x0000, 1, slave_addr)
            results['tests'].append(('Basic Read', 'PASS', 'Communication OK'))
        except ModbusException as e:
            results['tests'].append(('Basic Read', 'FAIL', str(e)))
            
            if e.code == ModbusException.ERROR_NO_RESPONSE:
                results['recommendations'].append(
                    "No response - Check: RS485 wiring (A+/B-), termination resistor (120ohm), "
                    "slave power, slave address setting, baud rate (9600)"
                )
            elif e.code == ModbusException.ERROR_CRC:
                results['recommendations'].append(
                    "CRC errors - Check: wiring quality, cable length (<1000m), "
                    "baud rate match, electrical noise shielding"
                )
            elif e.code == 0x02:  # Illegal address
                results['recommendations'].append(
                    "Address error - Register 0x0000 not supported, try different register"
                )
        except Exception:
            pass
        finally:
            self.auto_retry = old_auto_retry
        
        # Test 2: Response timing
        if self.stats.successful > 0:
            avg_ms = self.stats.avg_response_time
            if avg_ms > 500:
                results['recommendations'].append(
                    f"Slow response ({avg_ms:.0f}ms) - Check slave processing load, "
                    "reduce polling frequency, check for bus collisions"
                )
            results['tests'].append(('Response Time', 'INFO', f'Avg: {avg_ms:.1f}ms'))
        
        # Test 3: Error rate
        if self.stats.total_requests > 10:
            error_rate = 100 - self.stats.success_rate
            if error_rate > 5:
                results['recommendations'].append(
                    f"High error rate ({error_rate:.1f}%) - Check electrical connections, "
                    "cable shielding, grounding"
                )
            results['tests'].append(('Error Rate', 'INFO', f'{error_rate:.1f}%'))
        
        return results
    
    def get_diagnostic_report(self):
        """Generate comprehensive diagnostic report"""
        lines = [
            "",
            "=" * 70,
            "MODBUS DIAGNOSTIC REPORT",
            "=" * 70,
            f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Master Version: {self.VERSION}",
            f"Default Slave: {self.slave_address}",
            f"Timeout: {self.timeout}s",
            f"Auto Retry: {self.auto_retry} (max {self.max_retries})",
            "",
        ]
        
        # Add statistics
        lines.append(self.stats.get_summary())
        
        # Recent errors
        recent_errors = self.stats.get_recent_errors(5)
        if recent_errors:
            lines.append("")
            lines.append("RECENT ERRORS:")
            lines.append("-" * 50)
            for err in recent_errors:
                lines.append(
                    f"  {err['time'].strftime('%H:%M:%S')} "
                    f"Slave {err['slave']} FC=0x{err['func']:02X} "
                    f"Addr=0x{err['addr']:04X} -> {err['error']}"
                )
        
        # Last transaction
        last = self.get_last_transaction()
        lines.append("")
        lines.append("LAST TRANSACTION:")
        lines.append("-" * 50)
        lines.append(f"  TX: {last['tx'] or 'N/A'}")
        lines.append(f"  RX: {last['rx'] or 'N/A'}")
        if last['error']:
            lines.append(f"  Error: {last['error']}")
        
        lines.append("=" * 70)
        
        return "\n".join(lines)
