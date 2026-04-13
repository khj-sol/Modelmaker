#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
RS485 Channel Wrapper for Modbus
Version: 1.1.0

Changes in 1.1.0:
- Pi Zero 2W and Pi 5 dual compatibility
- Compatible with config.py v1.0.9 auto-detection

Changes in 1.0.9:
- Updated for multi-platform support (Pi Zero/3/4/5)
- Compatible with config.py v1.0.8 auto-detection

Changes in 1.0.8:
- Fixed flush_rx() aggressive FCR reset truncating response data
- Removed FCR FIFO reset method (was deleting valid response bytes)

Changes in 1.0.7:
- Added RS232 RS485 CAN HAT+ support (Auto Direction Control)
- Removed manual TXDEN GPIO control (handled by HAT hardware)

Changes in 1.0.6:
- Fixed to use lgpio instead of RPi.GPIO for Raspberry Pi 5 compatibility

Changes in 1.4.0:
- Added HAT_TRANSACTION_LOCK for Modbus transaction-level synchronization
- Added DER-AVM priority support (CH2 has priority over CH1)
- CH1 waits if DER-AVM (CH2) is communicating

Changes in 1.3.0:
- Added flush_rx() method for complete RX buffer clearing
- Added get_rx_level() method to check FIFO status
- Improved timing between operations
"""

import sys
import os
import time
import threading
import lgpio

# Add library path
libdir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(libdir)

from waveshare_2_CH_RS485_HAT import RS485

# SC16IS752 Register constants
CMD_READ = 0x80
CMD_WRITE = 0x00
RHR = 0x00  # RX FIFO
THR = 0x00  # TX FIFO
FCR = 0x02  # FIFO Control Register
RXLVL = 0x09  # RX FIFO level
TXLVL = 0x08  # TX FIFO level

def REG(x):
    return x << 3

# =============================================================================
# HAT-level Transaction Lock (shared between CH1 and CH2)
# DER-AVM (CH2) has priority over CH1
# =============================================================================
HAT_TRANSACTION_LOCK = threading.RLock()
DER_AVM_ACTIVE = threading.Event()  # Set when DER-AVM is in transaction


def acquire_hat_lock(channel, timeout=5.0):
    """
    Acquire HAT transaction lock with DER-AVM priority.
    
    Args:
        channel: 1 or 2
        timeout: Maximum wait time in seconds
        
    Returns:
        bool: True if lock acquired, False if timeout
    """
    start = time.time()
    
    # CH1 must wait if DER-AVM (CH2) is active
    if channel == 1:
        while DER_AVM_ACTIVE.is_set():
            if time.time() - start > timeout:
                return False
            time.sleep(0.01)
    
    # Try to acquire the lock
    acquired = HAT_TRANSACTION_LOCK.acquire(timeout=max(0.1, timeout - (time.time() - start)))
    
    # CH2 (DER-AVM) sets the active flag
    if acquired and channel == 2:
        DER_AVM_ACTIVE.set()
    
    return acquired


def release_hat_lock(channel):
    """
    Release HAT transaction lock.
    
    Args:
        channel: 1 or 2
    """
    # CH2 (DER-AVM) clears the active flag
    if channel == 2:
        DER_AVM_ACTIVE.clear()
    
    try:
        HAT_TRANSACTION_LOCK.release()
    except RuntimeError:
        pass  # Already released

class RS485Channel:
    def __init__(self, rs485_obj, channel_num):
        """
        Initialize RS485 Channel Wrapper
        
        Args:
            rs485_obj: RS485 HAT object
            channel_num: 1 or 2
        """
        self.rs485 = rs485_obj
        self.channel = channel_num
        self._h = rs485_obj.config._h  # GPIO handle for lgpio
        
        # Get SC16IS752 object directly
        if channel_num == 1:
            self.sc16is752 = rs485_obj.SC16IS752_CH1
            self.txden = rs485_obj.config.TXDEN_1
            self.CHANNEL = 0x00
        else:
            self.sc16is752 = rs485_obj.SC16IS752_CH2
            self.txden = rs485_obj.config.TXDEN_2
            self.CHANNEL = 0x02
    
    def get_rx_level(self):
        """Get number of bytes in RX FIFO"""
        try:
            cmd = CMD_READ | REG(RXLVL) | self.CHANNEL
            return self.sc16is752.WR_REG(cmd, 0xff)[0]
        except:
            return 0
    
    def flush_rx(self):
        """Flush RX FIFO by reading all available bytes (no FCR reset)"""
        flushed = 0
        try:
            # Read all bytes from FIFO without FCR reset
            # FCR reset was causing response data truncation
            for _ in range(64):  # SC16IS752 FIFO is 64 bytes max
                rxlvl = self.get_rx_level()
                if rxlvl == 0:
                    break
                # Read bytes one by one
                cmd = CMD_READ | REG(RHR) | self.CHANNEL
                for _ in range(rxlvl):
                    self.sc16is752.WR_REG(cmd, 0xff)
                    flushed += 1
                time.sleep(0.001)
            
            # NOTE: Removed FCR reset (0x07) - it was too aggressive
            # and was deleting valid response bytes
            
        except Exception as e:
            pass
        
        return flushed
    
    def write(self, data):
        """Write data to channel"""
        if isinstance(data, (list, tuple)):
            data = bytes(data)
        elif isinstance(data, str):
            data = data.encode('latin-1')
        
        # Convert bytes to string for HAT library
        data_str = data.decode('latin-1')
        
        if self.channel == 1:
            self.rs485.RS485_CH1_Write(data_str)
        else:
            self.rs485.RS485_CH2_Write(data_str)
    
    def read_byte(self):
        """Read single byte from channel (non-blocking)"""
        try:
            # Read RX FIFO level
            rxlvl = self.get_rx_level()
            
            if rxlvl > 0:
                # Read one byte from RX FIFO
                # Note: RS232 RS485 CAN HAT+ uses Auto Direction Control
                # No manual TXDEN GPIO control needed
                cmd = CMD_READ | REG(RHR) | self.CHANNEL
                byte_val = self.sc16is752.WR_REG(cmd, 0xff)[0]
                return chr(byte_val)
            else:
                return None
                
        except Exception as e:
            return None
    
    def read(self, length):
        """Read multiple bytes from channel"""
        result = ""
        for i in range(length):
            byte = self.read_byte()
            if byte:
                result += byte
            else:
                break
            time.sleep(0.001)
        return result
