#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""
Modbus RTU Utility Functions
Version: 1.0.6
"""

def calculate_crc16(data):
    """
    Calculate Modbus RTU CRC-16
    
    Args:
        data: bytes or list of integers
    
    Returns:
        int: CRC-16 value (16-bit)
    """
    if isinstance(data, (list, tuple)):
        data = bytes(data)
    
    crc = 0xFFFF
    
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    
    return crc

def add_crc(data):
    """
    Add CRC-16 to Modbus frame
    
    Args:
        data: list of integers
    
    Returns:
        list: data with CRC appended (CRC Low, CRC High)
    """
    crc = calculate_crc16(data)
    crc_low = crc & 0xFF
    crc_high = (crc >> 8) & 0xFF
    return data + [crc_low, crc_high]

def verify_crc(frame):
    """
    Verify CRC-16 of Modbus frame
    
    Args:
        frame: bytes or list with CRC at end
    
    Returns:
        bool: True if CRC is valid
    """
    if len(frame) < 4:  # Minimum: address + function + CRC(2)
        return False
    
    data = frame[:-2]
    received_crc = frame[-2] | (frame[-1] << 8)
    calculated_crc = calculate_crc16(data)
    
    return received_crc == calculated_crc

def bytes_to_hex(data):
    """
    Convert bytes/list to hex string for display
    
    Args:
        data: bytes or list
    
    Returns:
        str: hex string
    """
    if isinstance(data, (list, tuple)):
        return ' '.join(f'{b:02X}' for b in data)
    else:
        return ' '.join(f'{b:02X}' for b in data)
