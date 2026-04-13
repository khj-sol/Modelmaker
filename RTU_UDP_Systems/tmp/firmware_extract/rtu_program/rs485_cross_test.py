#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RS485 HAT CH1-CH2 Cross Communication Test
Version: 1.0.7

Changes in 1.0.7:
- Fixed read_with_timeout() for Pi Zero compatibility
- Uses direct RXLVL polling instead of IRQ-based reading
- Supports all Pi models (Pi Zero/3/4/5)

Tests communication between CH1 and CH2 of Waveshare 2-CH RS485 HAT.
Connect CH1 A-B to CH2 A-B for loopback test.

Wiring:
  CH1 A ---- CH2 A
  CH1 B ---- CH2 B
  CH1 GND -- CH2 GND (common ground)

Usage:
  python rs485_cross_test.py
"""

import sys
import os
import time
import threading

# Add library path
libdir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'lib')
sys.path.append(libdir)
hatdir = os.path.join(libdir, 'waveshare_2_CH_RS485_HAT')
sys.path.append(hatdir)

try:
    from RS485 import RS485
    import RPi.GPIO as GPIO
    HAT_AVAILABLE = True
except (ImportError, RuntimeError) as e:
    print(f"[ERROR] Cannot import HAT library: {e}")
    HAT_AVAILABLE = False


class RS485CrossTest:
    """RS485 CH1-CH2 Cross Communication Tester"""
    
    def __init__(self, baudrate: int = 9600):
        self.baudrate = baudrate
        self.rs485 = None
        self.running = False
        
        # Statistics
        self.ch1_tx_count = 0
        self.ch1_rx_count = 0
        self.ch2_tx_count = 0
        self.ch2_rx_count = 0
        self.error_count = 0
    
    def init(self) -> bool:
        """Initialize RS485 HAT"""
        if not HAT_AVAILABLE:
            print("[ERROR] RS485 HAT library not available")
            return False
        
        try:
            print(f"\n{'='*60}")
            print("  Initializing RS485 HAT...")
            print(f"{'='*60}")
            
            self.rs485 = RS485()
            
            print(f"  CH1: Initializing @ {self.baudrate} bps...")
            self.rs485.RS485_CH1_begin(self.baudrate)
            print("  CH1: OK")
            
            print(f"  CH2: Initializing @ {self.baudrate} bps...")
            self.rs485.RS485_CH2_begin(self.baudrate)
            print("  CH2: OK")
            
            # Store SC16IS752 objects for direct register access
            self._ch1 = self.rs485.SC16IS752_CH1
            self._ch2 = self.rs485.SC16IS752_CH2
            
            print(f"{'='*60}")
            print("  RS485 HAT initialized successfully!")
            print(f"{'='*60}\n")
            
            return True
            
        except Exception as e:
            print(f"[ERROR] Init failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _get_rx_level(self, channel: int) -> int:
        """Get RX FIFO level for a channel using RXLVL register"""
        try:
            CMD_READ = 0x80
            RXLVL = 0x09
            REG = lambda x: x << 3
            
            if channel == 1:
                ch_offset = 0x00  # CHANNEL_1
                result = self._ch1.WR_REG(CMD_READ | REG(RXLVL) | ch_offset, 0xFF)
            else:
                ch_offset = 0x02  # CHANNEL_2
                result = self._ch2.WR_REG(CMD_READ | REG(RXLVL) | ch_offset, 0xFF)
            
            if result and len(result) > 0:
                return result[0]
            return 0
        except Exception as e:
            return 0
    
    def has_data_ch1(self) -> bool:
        """Check if CH1 has data available using RXLVL"""
        return self._get_rx_level(1) > 0
    
    def has_data_ch2(self) -> bool:
        """Check if CH2 has data available using RXLVL"""
        return self._get_rx_level(2) > 0
    
    def read_with_timeout(self, channel: int, expected_len: int, timeout: float = 2.0) -> bytes:
        """Read data from channel with timeout using direct FIFO polling (Pi Zero compatible)"""
        received = bytearray()
        start_time = time.time()
        
        # SC16IS752 register constants
        CMD_READ = 0x80
        RHR = 0x00
        REG = lambda x: x << 3
        
        if channel == 1:
            ch_offset = 0x00
            sc16is752 = self._ch1
        else:
            ch_offset = 0x02
            sc16is752 = self._ch2
        
        while (time.time() - start_time) < timeout:
            try:
                # Check RXLVL directly instead of using IRQ
                rxlvl = self._get_rx_level(channel)
                
                if rxlvl > 0:
                    # Read byte directly from RHR register
                    result = sc16is752.WR_REG(CMD_READ | REG(RHR) | ch_offset, 0xFF)
                    if result and len(result) > 0:
                        received.append(result[0])
                        print(f".", end="", flush=True)
                        if len(received) >= expected_len:
                            break
                else:
                    time.sleep(0.005)  # Small delay if no data
            except Exception as e:
                pass
            time.sleep(0.001)
        
        print()  # newline after dots
        return bytes(received)
    
    def raw_test(self):
        """Raw low-level test with maximum debugging"""
        print(f"\n{'='*60}")
        print("  RAW LOW-LEVEL TEST")
        print(f"{'='*60}")
        
        # Test CH1 -> CH2
        print("\n  [1] Testing CH1 -> CH2:")
        print("      Sending 'TEST' from CH1...")
        
        # Send from CH1
        test_data = "TEST"
        self.rs485.RS485_CH1_Write(test_data)
        
        # Wait for transmission
        time.sleep(0.1)
        
        # Receive with timeout method
        print("      Receiving on CH2: ", end="", flush=True)
        received = self.read_with_timeout(2, len(test_data), timeout=2.0)
        
        if received:
            try:
                received_str = received.decode('ascii')
                print(f"      CH2 received: '{received_str}' ({received.hex().upper()})")
                if received_str == test_data:
                    print("      *** CH1 -> CH2: SUCCESS ***")
                else:
                    print(f"      *** MISMATCH: expected '{test_data}' ***")
            except:
                print(f"      CH2 received (hex): {received.hex().upper()}")
        else:
            print("      *** NO DATA RECEIVED ***")
        
        time.sleep(0.2)
        
        # Test CH2 -> CH1
        print("\n  [2] Testing CH2 -> CH1:")
        print("      Sending 'ABCD' from CH2...")
        
        # Send from CH2
        test_data = "ABCD"
        self.rs485.RS485_CH2_Write(test_data)
        
        # Wait for transmission
        time.sleep(0.1)
        
        # Receive with timeout method
        print("      Receiving on CH1: ", end="", flush=True)
        received = self.read_with_timeout(1, len(test_data), timeout=2.0)
        
        if received:
            try:
                received_str = received.decode('ascii')
                print(f"      CH1 received: '{received_str}' ({received.hex().upper()})")
                if received_str == test_data:
                    print("      *** CH2 -> CH1: SUCCESS ***")
                else:
                    print(f"      *** MISMATCH: expected '{test_data}' ***")
            except:
                print(f"      CH1 received (hex): {received.hex().upper()}")
        else:
            print("      *** NO DATA RECEIVED ***")
        
        # Check GPIO states
        print("\n  [3] GPIO Status:")
        try:
            txden1 = GPIO.input(27)  # TXDEN_1
            txden2 = GPIO.input(22)  # TXDEN_2
            irq = GPIO.input(24)     # IRQ
            print(f"      TXDEN_1 (GPIO27): {txden1} ({'TX' if txden1==0 else 'RX'})")
            print(f"      TXDEN_2 (GPIO22): {txden2} ({'TX' if txden2==0 else 'RX'})")
            print(f"      IRQ     (GPIO24): {irq} ({'Data Available' if irq==0 else 'No Data'})")
        except Exception as e:
            print(f"      GPIO read error: {e}")
        
        print(f"\n{'='*60}")
    
    def test_ch1_to_ch2(self, data: bytes) -> bool:
        """Send data from CH1 to CH2"""
        print(f"\n  [CH1 -> CH2] Sending: {data.hex().upper()}")
        
        # Convert bytes to string for Waveshare library
        data_str = ''.join(chr(b) for b in data)
        
        # Send from CH1
        self.rs485.RS485_CH1_Write(data_str)
        self.ch1_tx_count += 1
        
        # Wait for transmission
        time.sleep(0.1)
        
        # Receive on CH2 using try/except timeout method
        print("  [CH1 -> CH2] Receiving: ", end="", flush=True)
        received = self.read_with_timeout(2, len(data), timeout=2.0)
        
        if received:
            self.ch2_rx_count += 1
            print(f"  [CH1 -> CH2] Received: {received.hex().upper()}")
            
            if bytes(received) == data:
                print("  [CH1 -> CH2] *** MATCH OK ***")
                return True
            else:
                print("  [CH1 -> CH2] *** MISMATCH ***")
                self.error_count += 1
                return False
        else:
            print("  [CH1 -> CH2] *** NO RESPONSE (Timeout) ***")
            self.error_count += 1
            return False
    
    def test_ch2_to_ch1(self, data: bytes) -> bool:
        """Send data from CH2 to CH1"""
        print(f"\n  [CH2 -> CH1] Sending: {data.hex().upper()}")
        
        # Convert bytes to string for Waveshare library
        data_str = ''.join(chr(b) for b in data)
        
        # Send from CH2
        self.rs485.RS485_CH2_Write(data_str)
        self.ch2_tx_count += 1
        
        # Wait for transmission
        time.sleep(0.1)
        
        # Receive on CH1 using try/except timeout method
        print("  [CH2 -> CH1] Receiving: ", end="", flush=True)
        received = self.read_with_timeout(1, len(data), timeout=2.0)
        
        if received:
            self.ch1_rx_count += 1
            print(f"  [CH2 -> CH1] Received: {received.hex().upper()}")
            
            if bytes(received) == data:
                print("  [CH2 -> CH1] *** MATCH OK ***")
                return True
            else:
                print("  [CH2 -> CH1] *** MISMATCH ***")
                self.error_count += 1
                return False
        else:
            print("  [CH2 -> CH1] *** NO RESPONSE (Timeout) ***")
            self.error_count += 1
            return False
    
    def run_basic_test(self):
        """Run basic cross communication test"""
        print(f"\n{'='*60}")
        print("  Basic Cross Communication Test")
        print(f"{'='*60}")
        print("  Wiring: CH1 A-B <--> CH2 A-B")
        print(f"{'='*60}")
        
        # Test 1: Simple bytes CH1 -> CH2
        print("\n  Test 1: CH1 -> CH2 (Simple)")
        self.test_ch1_to_ch2(b'\x01\x02\x03\x04\x05')
        time.sleep(0.2)
        
        # Test 2: Simple bytes CH2 -> CH1
        print("\n  Test 2: CH2 -> CH1 (Simple)")
        self.test_ch2_to_ch1(b'\x0A\x0B\x0C\x0D\x0E')
        time.sleep(0.2)
        
        # Test 3: Modbus-like frame CH1 -> CH2
        print("\n  Test 3: CH1 -> CH2 (Modbus Frame)")
        modbus_req = bytes([0x01, 0x03, 0x03, 0xE8, 0x00, 0x16, 0x44, 0x74])
        self.test_ch1_to_ch2(modbus_req)
        time.sleep(0.2)
        
        # Test 4: Modbus-like frame CH2 -> CH1
        print("\n  Test 4: CH2 -> CH1 (Modbus Response)")
        modbus_resp = bytes([0x01, 0x03, 0x04, 0x00, 0x64, 0x00, 0xC8, 0xFA, 0x6D])
        self.test_ch2_to_ch1(modbus_resp)
        time.sleep(0.2)
        
        # Test 5: All bytes 0x00 ~ 0xFF
        print("\n  Test 5: CH1 -> CH2 (All Bytes 0x00~0x0F)")
        self.test_ch1_to_ch2(bytes(range(16)))
        time.sleep(0.2)
        
        self.print_statistics()
    
    def run_continuous_test(self, count: int = 100, interval: float = 0.5):
        """Run continuous ping-pong test"""
        print(f"\n{'='*60}")
        print(f"  Continuous Test ({count} iterations)")
        print(f"{'='*60}")
        
        success = 0
        fail = 0
        
        for i in range(count):
            # Alternate between CH1->CH2 and CH2->CH1
            test_data = bytes([i & 0xFF, (i >> 8) & 0xFF, 0xAA, 0x55])
            
            if i % 2 == 0:
                if self.test_ch1_to_ch2(test_data):
                    success += 1
                else:
                    fail += 1
            else:
                if self.test_ch2_to_ch1(test_data):
                    success += 1
                else:
                    fail += 1
            
            time.sleep(interval)
            
            # Progress
            if (i + 1) % 10 == 0:
                print(f"\n  Progress: {i+1}/{count} (Success: {success}, Fail: {fail})")
        
        print(f"\n{'='*60}")
        print(f"  Continuous Test Complete")
        print(f"  Success: {success}/{count} ({100*success/count:.1f}%)")
        print(f"  Fail: {fail}/{count}")
        print(f"{'='*60}")
        
        self.print_statistics()
    
    def run_echo_mode(self):
        """Run echo server mode - CH2 echoes back what CH1 sends"""
        print(f"\n{'='*60}")
        print("  Echo Mode: CH1 sends, CH2 echoes back")
        print("  Press Ctrl+C to stop")
        print(f"{'='*60}")
        
        self.running = True
        
        # Start CH2 echo thread
        def echo_thread():
            while self.running:
                try:
                    if self.has_data_ch2():
                        byte_data = self.rs485.RS485_CH2_ReadByte()
                        if byte_data:
                            val = ord(byte_data) if isinstance(byte_data, str) else byte_data
                            # Echo back - convert to string for Waveshare library
                            self.rs485.RS485_CH2_Write(chr(val))
                            self.ch2_rx_count += 1
                            self.ch2_tx_count += 1
                            print(f"  [CH2 Echo] 0x{val:02X}")
                    else:
                        time.sleep(0.001)
                except Exception as e:
                    print(f"  [CH2 Echo Error] {e}")
        
        echo_t = threading.Thread(target=echo_thread, daemon=True)
        echo_t.start()
        
        try:
            while True:
                user_input = input("\n  Enter hex bytes to send from CH1 (e.g., 01 02 03): ").strip()
                if not user_input:
                    continue
                
                try:
                    data = bytes.fromhex(user_input.replace(' ', ''))
                    print(f"  [CH1 TX] {data.hex().upper()}")
                    # Convert to string for Waveshare library
                    self.rs485.RS485_CH1_Write(''.join(chr(b) for b in data))
                    self.ch1_tx_count += 1
                    
                    # Wait for echo
                    time.sleep(0.2)
                    
                    received = bytearray()
                    timeout = time.time() + 1.0
                    while time.time() < timeout:
                        if self.has_data_ch1():
                            byte_data = self.rs485.RS485_CH1_ReadByte()
                            if byte_data:
                                received.append(ord(byte_data) if isinstance(byte_data, str) else byte_data)
                        else:
                            if len(received) > 0:
                                time.sleep(0.05)
                                if not self.has_data_ch1():
                                    break
                            time.sleep(0.01)
                    
                    if received:
                        self.ch1_rx_count += 1
                        print(f"  [CH1 RX] {received.hex().upper()}")
                        if bytes(received) == data:
                            print("  *** Echo OK ***")
                        else:
                            print("  *** Echo Mismatch ***")
                    else:
                        print("  *** No Echo ***")
                        
                except ValueError as e:
                    print(f"  [ERROR] Invalid hex: {e}")
                    
        except KeyboardInterrupt:
            pass
        
        self.running = False
        time.sleep(0.2)
        self.print_statistics()
    
    def print_statistics(self):
        """Print communication statistics"""
        print(f"\n{'='*60}")
        print("  Communication Statistics")
        print(f"{'='*60}")
        print(f"  CH1 TX: {self.ch1_tx_count}")
        print(f"  CH1 RX: {self.ch1_rx_count}")
        print(f"  CH2 TX: {self.ch2_tx_count}")
        print(f"  CH2 RX: {self.ch2_rx_count}")
        print(f"  Errors: {self.error_count}")
        print(f"{'='*60}")


def print_menu():
    """Print test menu"""
    print(f"\n{'='*60}")
    print("  RS485 HAT CH1-CH2 Cross Test Menu")
    print(f"{'='*60}")
    print("  1. Basic Test (5 tests)")
    print("  2. Continuous Test (100 iterations)")
    print("  3. Echo Mode (interactive)")
    print("  4. Single CH1 -> CH2")
    print("  5. Single CH2 -> CH1")
    print("  6. Show Statistics")
    print("  7. RAW Test (low-level debug)")
    print("  8. Check RXLVL registers")
    print("  0. Exit")
    print(f"{'='*60}")


def main():
    print(f"\n{'='*60}")
    print("  RS485 HAT CH1-CH2 Cross Communication Test")
    print("  Version 1.0.1")
    print(f"{'='*60}")
    print("\n  Wiring Required:")
    print("    CH1 A ---- CH2 A")
    print("    CH1 B ---- CH2 B")
    print("    (GND is common)")
    print(f"{'='*60}")
    
    if not HAT_AVAILABLE:
        print("\n[ERROR] RS485 HAT not available. Exiting.")
        sys.exit(1)
    
    tester = RS485CrossTest(baudrate=9600)
    
    if not tester.init():
        print("\n[ERROR] Failed to initialize. Exiting.")
        sys.exit(1)
    
    while True:
        print_menu()
        
        try:
            choice = input("  Select [0-8]: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        
        if choice == '0':
            break
        elif choice == '1':
            tester.run_basic_test()
        elif choice == '2':
            try:
                count = int(input("  Enter iteration count [100]: ") or "100")
                tester.run_continuous_test(count)
            except ValueError:
                tester.run_continuous_test(100)
        elif choice == '3':
            tester.run_echo_mode()
        elif choice == '4':
            try:
                data = input("  Enter hex bytes (e.g., 01 02 03): ").strip()
                data = bytes.fromhex(data.replace(' ', ''))
                tester.test_ch1_to_ch2(data)
            except ValueError as e:
                print(f"  [ERROR] Invalid hex: {e}")
        elif choice == '5':
            try:
                data = input("  Enter hex bytes (e.g., 01 02 03): ").strip()
                data = bytes.fromhex(data.replace(' ', ''))
                tester.test_ch2_to_ch1(data)
            except ValueError as e:
                print(f"  [ERROR] Invalid hex: {e}")
        elif choice == '6':
            tester.print_statistics()
        elif choice == '7':
            # RAW low-level test
            tester.raw_test()
        elif choice == '8':
            # Debug: Check RXLVL
            print(f"\n  [DEBUG] CH1 RXLVL = {tester._get_rx_level(1)}")
            print(f"  [DEBUG] CH2 RXLVL = {tester._get_rx_level(2)}")
            print("\n  Sending 0xAA from CH1...")
            tester.rs485.RS485_CH1_Write(chr(0xAA))
            time.sleep(0.1)
            print(f"  [DEBUG] CH2 RXLVL after TX = {tester._get_rx_level(2)}")
            print("\n  Sending 0x55 from CH2...")
            tester.rs485.RS485_CH2_Write(chr(0x55))
            time.sleep(0.1)
            print(f"  [DEBUG] CH1 RXLVL after TX = {tester._get_rx_level(1)}")
        else:
            print("  [ERROR] Invalid selection")
    
    print("\n  Goodbye!")


if __name__ == '__main__':
    main()
