#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
RS485 Communication Handler for Waveshare 2-CH RS485 HAT
Version: 1.1.0 - Multi-Platform Support (Pi Zero 2W / Pi 5 Dual Compatible)

Changes in 1.1.0:
  - Added auto_direction_control check before GPIO writes
  - Full Pi Zero 2W and Pi 5 dual compatibility
  - Safe GPIO operations with fallback to auto mode

Changes in 1.0.9:
  - Updated for multi-platform support (Pi Zero/3/4/5)
  - Compatible with config.py v1.0.8 auto-detection

Changes in 1.0.8:
  - Fixed aggressive FIFO flush truncating response data
  - Changed from FCR reset to byte-count discard method
  - _flush_rx_fifo() replaced with _discard_tx_echo(byte_count)

Changes in 1.0.7:
  - Added TX echo removal for RS232 RS485 CAN HAT+ (Auto Direction Control)
  - After TX, flush echo bytes from RX FIFO
"""
import lgpio
import time
import os
import sys
libdir = os.path.dirname(os.path.realpath(__file__))
if os.path.exists(libdir):
    sys.path.append(libdir)

import config
import SC16IS752

# SC16IS752 Register definitions for echo flush
CMD_READ = 0x80
CMD_WRITE = 0x00
RXLVL = 0x09
RHR = 0x00
FCR = 0x02

def REG(x):
    return x << 3

class RS485(object):
    def __init__(self):
        self.config = config.config()
        self._h = self.config._h  # GPIO handle from config
        self._auto_dir = getattr(self.config, 'auto_direction_control', True)
        
    def _safe_gpio_write(self, pin, value):
        """Safe GPIO write - skips if auto direction control is enabled"""
        if self._auto_dir:
            return  # Auto Direction Control HAT handles this
        try:
            lgpio.gpio_write(self._h, pin, value)
        except Exception:
            pass  # Ignore GPIO errors (pin not claimed or busy)
        
    def RS485_CH1_begin(self, Baud):
        self.SC16IS752_CH1 = SC16IS752.SC16IS752(self.config, 1)  # Pass config instance
        self.SC16IS752_CH1.Set_Baudrate(Baud)
        self._safe_gpio_write(self.config.TXDEN_1, 1)
        print('SC16IS752_CH1')
        
    def RS485_CH2_begin(self, Baud):
        self.SC16IS752_CH2 = SC16IS752.SC16IS752(self.config, 2)  # Pass config instance
        self.SC16IS752_CH2.Set_Baudrate(Baud)
        self._safe_gpio_write(self.config.TXDEN_2, 1)
    
    def _discard_tx_echo(self, channel, tx_byte_count):
        """Discard TX echo bytes from RX FIFO (exact count, preserves response data)"""
        ch = SC16IS752.CHANNEL_1 if channel == 1 else SC16IS752.CHANNEL_2
        time.sleep(0.003)  # Wait for echo to arrive in FIFO
        # Read and discard only TX echo bytes (not entire FIFO)
        for _ in range(tx_byte_count):
            self.config.SPI_transmission_nByte([CMD_READ | REG(RHR) | ch, 0x00])
    
    def _flush_rx_fifo(self, channel):
        """DEPRECATED: Use _discard_tx_echo() instead. Kept for compatibility."""
        # Do nothing - aggressive flush was truncating response data
        pass
        
    def RS485_CH1_ReadByte(self):
        self._safe_gpio_write(self.config.TXDEN_1, 1)
        return self.SC16IS752_CH1.UART_ReadByte()
        
    def RS485_CH2_ReadByte(self):
        self._safe_gpio_write(self.config.TXDEN_2, 1)
        return self.SC16IS752_CH2.UART_ReadByte()
        
    def RS485_CH1_Write(self, pData):
        self._safe_gpio_write(self.config.TXDEN_1, 0)
        self.SC16IS752_CH1.UART_Write(pData)
        time.sleep(0.005)  # Waiting to send
        self._safe_gpio_write(self.config.TXDEN_1, 1)
        # Discard TX echo for RS232 RS485 CAN HAT+ (exact byte count)
        self._discard_tx_echo(1, len(pData))
        
    def RS485_CH2_Write(self, pData):
        self._safe_gpio_write(self.config.TXDEN_2, 0)
        self.SC16IS752_CH2.UART_Write(pData)
        time.sleep(0.005)  # Waiting to send
        self._safe_gpio_write(self.config.TXDEN_2, 1)
        # Discard TX echo for RS232 RS485 CAN HAT+ (exact byte count)
        self._discard_tx_echo(2, len(pData))
    
    def RS485_CH1_Read(self, Len):
        self._safe_gpio_write(self.config.TXDEN_1, 1)
        return self.SC16IS752_CH1.UART_Read(Len)
        
    def RS485_CH2_Read(self, Len):
        self._safe_gpio_write(self.config.TXDEN_2, 1)
        return self.SC16IS752_CH2.UART_Read(Len)
    

    def RS485_CH1_EnableSleep(self):
        self.SC16IS752_CH1.EnableSleep()
        
    def RS485_CH2_EnableSleep(self):
        self.SC16IS752_CH2.EnableSleep()
    
    def RS485_CH1_DisableSleep(self):
        self.SC16IS752_CH1.DisableSleep()
        
    def RS485_CH2_DisableSleep(self):
        self.SC16IS752_CH2.DisableSleep()
        