#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
RS485 HAT Configuration for Raspberry Pi (All Models)
Version: 1.0.9 - Multi-Platform Support (Pi Zero 2W / Pi 5 Dual Compatible)

Supported Platforms:
  - Raspberry Pi Zero / Zero W / Zero 2 W (gpiochip0)
  - Raspberry Pi 3B / 3B+ (gpiochip0)
  - Raspberry Pi 4B (gpiochip0)
  - Raspberry Pi 5 (gpiochip4)

Supported HATs:
  - Waveshare 2-CH RS485 HAT (IRQ=GPIO24, Manual TXDEN)
  - RS232 RS485 CAN HAT+ (IRQ=GPIO22, Auto Direction Control)

Changes in 1.0.9:
  - Added TXDEN pin claim with fallback to Auto Direction Control
  - Full Pi Zero 2W and Pi 5 dual compatibility
  - auto_direction_control flag for runtime GPIO control decision

Changes in 1.0.8:
  - Added automatic Raspberry Pi model detection
  - Dynamic GPIO chip selection (gpiochip0 for Pi Zero/3/4, gpiochip4 for Pi 5)
  - Full compatibility with all Raspberry Pi models

Changes in 1.0.7:
  - Added RS232 RS485 CAN HAT+ support
  - IRQ_PIN changed to GPIO22 (RS485_INT on new HAT)
  - TXDEN control changed to no-op (Auto Direction Control on new HAT)
  - Backward compatible with 2-CH RS485 HAT when configured
"""
import lgpio
import spidev as SPI
import threading
import logging


def get_pi_model():
    """
    Detect Raspberry Pi model from device tree.
    
    Returns:
        str: Model string (e.g., 'Raspberry Pi 5 Model B', 'Raspberry Pi Zero W')
    """
    try:
        with open('/proc/device-tree/model', 'r') as f:
            model = f.read().rstrip('\x00')
        return model
    except Exception:
        return "Unknown"


def get_gpio_chip_number():
    """
    Get correct GPIO chip number based on Raspberry Pi model.
    
    Pi 5 uses gpiochip4 (RP1 GPIO controller)
    Pi Zero/2/3/4 use gpiochip0 (BCM2835/BCM2711 GPIO controller)
    
    Returns:
        int: GPIO chip number (0 or 4)
    """
    model = get_pi_model().lower()
    if 'pi 5' in model:
        return 4  # Pi 5: gpiochip4 (RP1)
    else:
        return 0  # Pi Zero/2/3/4: gpiochip0 (BCM)


class config(object):
    # RS232 RS485 CAN HAT+ uses GPIO22 for RS485_INT
    # (2-CH RS485 HAT uses GPIO24)
    IRQ_PIN = 22
    
    # TXDEN pins (not used on RS232 RS485 CAN HAT+ - Auto Direction Control)
    # Kept for backward compatibility with 2-CH RS485 HAT
    TXDEN_1 = 27
    TXDEN_2 = 22
    
    _spi_lock = threading.Lock()
    
    # Singleton instance
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        # Skip if already initialized (singleton)
        if config._initialized:
            return
        config._initialized = True
        
        # Detect Pi model and select correct GPIO chip
        gpio_chip = get_gpio_chip_number()
        pi_model = get_pi_model()
        logging.info(f"Detected: {pi_model}, using gpiochip{gpio_chip}")
        
        self._h = lgpio.gpiochip_open(gpio_chip)
        self.pi_model = pi_model
        self.gpio_chip = gpio_chip
        
        # Auto Direction Control flag
        # True = HAT handles TX/RX direction automatically (no GPIO control needed)
        # False = Manual TXDEN control required (old 2-CH RS485 HAT)
        self.auto_direction_control = True
        
        # Claim IRQ pin as input
        lgpio.gpio_claim_input(self._h, self.IRQ_PIN)
        
        # Try to claim TXDEN pins as output
        # If successful, manual direction control is available
        # If failed (pin busy or HAT uses auto direction), use auto mode
        try:
            lgpio.gpio_claim_output(self._h, self.TXDEN_1, 1)  # Default RX mode (high)
            # Note: TXDEN_2 == IRQ_PIN (22), so skip claiming it
            if self.TXDEN_2 != self.IRQ_PIN:
                lgpio.gpio_claim_output(self._h, self.TXDEN_2, 1)
            self.auto_direction_control = False
            logging.info("TXDEN pins claimed - Manual Direction Control mode")
        except Exception as e:
            self.auto_direction_control = True
            logging.info(f"TXDEN claim skipped - Auto Direction Control mode: {e}")
        
        # Initialize SPI device
        import os
        if os.path.exists('/dev/spidev1.0'):
            self._spi = SPI.SpiDev(1, 0)
        elif os.path.exists('/dev/spidev1.1'):
            self._spi = SPI.SpiDev(1, 1)
        elif os.path.exists('/dev/spidev0.0'):
            self._spi = SPI.SpiDev(0, 0)
        else:
            raise FileNotFoundError("No SPI device found")
        self._spi.mode = 0b00
        self._spi.max_speed_hz = 1000000
    
    def TX_EN_1(self):
        """Enable TX for Channel 1 - No-op for Auto Direction Control HAT"""
        pass
    
    def TX_DIS_1(self):
        """Disable TX for Channel 1 - No-op for Auto Direction Control HAT"""
        pass
    
    def TX_EN_2(self):
        """Enable TX for Channel 2 - No-op for Auto Direction Control HAT"""
        pass
    
    def TX_DIS_2(self):
        """Disable TX for Channel 2 - No-op for Auto Direction Control HAT"""
        pass
    
    def SPI_transmission_nByte(self, value):
        with config._spi_lock:
            result = self._spi.xfer2(value)
        return result
