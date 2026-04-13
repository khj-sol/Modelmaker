#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RS485 Channel using Native UART (pyserial) for CM4-ETH-RS485-BASE-B
Version: 1.2.0

Drop-in replacement for RS485Channel (SPI HAT version).
Implements the same interface so ModbusMaster works unchanged.

Interface contract (used by ModbusMaster):
  - write(data: bytes)       -> send bytes
  - read_byte() -> str|None  -> read one byte, non-blocking
  - get_rx_level() -> int    -> number of bytes in RX buffer
  - flush_rx() -> int        -> flush RX buffer, return count
  - channel                  -> channel number attribute

RS485 Direction Control:
  Board is configured in Full-auto mode (0-ohm resistors).
  Hardware automatically switches TX/RX direction.
  No GPIO TXDEN control needed.
"""

import serial
import time
import logging


class RS485ChannelSerial:
    """
    RS485 Channel using native UART via pyserial.
    Compatible with ModbusMaster interface.
    """

    def __init__(self, port: str, baudrate: int = 9600, channel_num: int = 0):
        """
        Initialize RS485 channel on native UART port.

        Args:
            port: Serial port path (e.g., '/dev/ttyAMA3')
            baudrate: Communication speed (default 9600)
            channel_num: Channel number for identification (0-3)
        """
        self.port = port
        self.baudrate = baudrate
        self.channel = channel_num
        self.logger = logging.getLogger(f"RS485.COM{channel_num}")

        self.serial = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0,            # Non-blocking read
            write_timeout=1.0
        )

        # Flush any stale data
        self.serial.reset_input_buffer()
        self.serial.reset_output_buffer()

        self.logger.info(f"RS485 COM{channel_num} opened: {port} @ {baudrate}bps (Full-auto)")

    def write(self, data):
        """
        Write data to RS485 channel.
        Full-auto hardware handles TX/RX direction switching.

        Args:
            data: bytes, list, tuple, or str to send
        """
        if isinstance(data, (list, tuple)):
            data = bytes(data)
        elif isinstance(data, str):
            data = data.encode('latin-1')

        self.serial.write(data)
        self.serial.flush()

    def read_byte(self):
        """
        Read single byte from RS485 channel (non-blocking).

        Returns:
            str: Single character, or None if no data available
        """
        try:
            data = self.serial.read(1)
            if data and len(data) > 0:
                return chr(data[0])
            return None
        except Exception:
            return None

    def get_rx_level(self):
        """
        Get number of bytes waiting in receive buffer.

        Returns:
            int: Number of bytes available
        """
        try:
            return self.serial.in_waiting
        except Exception:
            return 0

    def flush_rx(self):
        """
        Flush receive buffer, discarding all pending data.

        Returns:
            int: Number of bytes flushed
        """
        try:
            count = self.serial.in_waiting
            if count > 0:
                self.serial.read(count)
            self.serial.reset_input_buffer()
            return count
        except Exception:
            return 0

    def close(self):
        """Close serial port."""
        try:
            if self.serial and self.serial.is_open:
                self.serial.close()
                self.logger.info(f"RS485 COM{self.channel} closed")
        except Exception:
            pass

    def is_open(self):
        """Check if serial port is open."""
        return self.serial and self.serial.is_open

    def set_baudrate(self, baudrate: int):
        """
        Change baudrate at runtime.

        Args:
            baudrate: New baudrate value
        """
        self.baudrate = baudrate
        self.serial.baudrate = baudrate
        self.logger.info(f"RS485 COM{self.channel} baudrate changed to {baudrate}")

    def __del__(self):
        self.close()
