#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CM4-ETH-RS485-BASE-B Configuration
Version: 1.0.0

Hardware: Waveshare CM4-ETH-RS485-BASE-B
  - 4x Isolated RS485 via native UART (no SPI, no SC16IS752)
  - Full-auto TX/RX direction control (hardware)
  - CM4 BCM2711 (gpiochip0)

Serial Port Mapping (Raspberry Pi OS Bookworm 64-bit):
  COM0: /dev/ttyAMA0  (GPIO14/15, UART0 PL011) - Debug / reserved
  COM1: /dev/ttyAMA3  (GPIO4/5,   UART3)       - Master (inverter/relay/weather)
  COM2: /dev/ttyAMA4  (GPIO8/9,   UART4)       - DER-AVM Slave
  COM3: /dev/ttyAMA5  (GPIO12/13, UART5)       - Future expansion

Note: dtoverlay=disable-bt required to free ttyAMA0 from Bluetooth

Boot config (/boot/firmware/config.txt) required:
  enable_uart=1
  dtoverlay=uart3
  dtoverlay=uart4
  dtoverlay=uart5
"""

import logging

# Serial port mapping for CM4-ETH-RS485-BASE-B (Bookworm)
# Requires: dtoverlay=disable-bt (frees ttyAMA0 from Bluetooth)
SERIAL_PORTS = {
    0: '/dev/ttyAMA0',    # COM0: UART0 PL011 GPIO14/15
    1: '/dev/ttyAMA3',    # COM1: UART3 GPIO4/5
    2: '/dev/ttyAMA4',    # COM2: UART4 GPIO8/9
    3: '/dev/ttyAMA5',    # COM3: UART5 GPIO12/13
}

# GPIO pin mapping (UART TX/RX)
GPIO_PINS = {
    0: {'tx': 14, 'rx': 15},
    1: {'tx': 4,  'rx': 5},
    2: {'tx': 8,  'rx': 9},
    3: {'tx': 12, 'rx': 13},
}

# TXDEN (TX Data Enable) GPIO pins for RS485 direction control
# Semi-auto mode: GPIO controls SP3485 DE/RE pins
#   LOW  = TX mode (driver enabled, receiver disabled)
#   HIGH = RX mode (driver disabled, receiver enabled)
# From Waveshare official example code
TXDEN_PINS = {
    0: 10,   # COM0: GPIO10
    1: 27,   # COM1: GPIO27
    2: 21,   # COM2: GPIO21
    3: 7,    # COM3: GPIO7
}

# User LED pins (for status indication)
USER_LED_0 = 20  # GPIO20
USER_LED_1 = 26  # GPIO26

# Buzzer pin
BUZZER_PIN = 22   # GPIO22

# Fan PWM pin
FAN_PIN = 18      # GPIO18


def get_platform():
    """
    Detect CM4 platform from device tree.

    Returns:
        str: Model string (e.g., 'Raspberry Pi Compute Module 4')
    """
    try:
        with open('/proc/device-tree/model', 'r') as f:
            model = f.read().rstrip('\x00')
        return model
    except Exception:
        return "Unknown"


def is_cm4():
    """
    Check if running on CM4 platform.

    Returns:
        bool: True if CM4 detected
    """
    model = get_platform().lower()
    return 'compute module 4' in model or 'cm4' in model


def get_serial_port(channel: int) -> str:
    """
    Get serial port device path for given channel number.

    Args:
        channel: RS485 channel number (0-3)

    Returns:
        str: Device path (e.g., '/dev/ttyS0')
    """
    port = SERIAL_PORTS.get(channel)
    if port is None:
        logging.warning(f"Invalid channel {channel}, falling back to COM0")
        port = SERIAL_PORTS[0]
    return port


def get_available_channels():
    """
    Check which serial ports are available on the system.

    Returns:
        dict: {channel_num: port_path} for available ports
    """
    import os
    available = {}
    for ch, port in SERIAL_PORTS.items():
        if os.path.exists(port):
            available[ch] = port
    return available
