#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SEM5046 Integrated Photovoltaic Environment Monitor Register Map
Modbus RTU Protocol - Function Code 04 (Input Register)
Version: 1.0.0

Communication: 9600bps, 8N1
Default Address: 0xFF
"""

class SEM5046RegisterMap:
    """SEM5046 Register Addresses"""
    
    # Standard Registers (FC04)
    AIR_TEMP = 0x0001          # Air temperature (+40, x100) -> /100-40 = ℃
    AIR_HUMIDITY = 0x0002      # Air humidity (x100) -> /100 = %RH
    AIR_PRESSURE = 0x0003      # Atmospheric pressure (x10) -> /10 = hPa
    WIND_SPEED = 0x0004        # Wind speed (x10) -> /10 = m/s
    WIND_DIRECTION = 0x0005    # Wind direction (x10) -> /10 = °
    MODULE_TEMP_1 = 0x0006     # Component temp 1 (+20, x100) -> /100-20 = ℃
    HORIZONTAL_RADIATION = 0x0007  # Total radiation = W/m²
    HORIZONTAL_ACCUM = 0x0008  # Cumulative total (x1000) -> /1000 = MJ/m²
    INCLINED_RADIATION = 0x000D    # Inclined radiation = W/m²
    INCLINED_ACCUM = 0x000E    # Cumulative inclined (x1000) -> /1000 = MJ/m²
    MODULE_TEMP_2 = 0x0011     # Component temp 2
    MODULE_TEMP_3 = 0x0012     # Component temp 3
    MODULE_TEMP_4 = 0x0013     # Component temp 4
    
    TOTAL_REGISTERS = 0x0020   # 32 registers


def raw_to_air_temp(raw: int) -> float:
    """Convert raw register to air temperature (℃)"""
    return raw / 100.0 - 40.0


def raw_to_humidity(raw: int) -> float:
    """Convert raw register to humidity (%RH)"""
    return raw / 100.0


def raw_to_pressure(raw: int) -> float:
    """Convert raw register to pressure (hPa)"""
    return raw / 10.0


def raw_to_wind_speed(raw: int) -> float:
    """Convert raw register to wind speed (m/s)"""
    return raw / 10.0


def raw_to_wind_direction(raw: int) -> float:
    """Convert raw register to wind direction (°)"""
    return raw / 10.0


def raw_to_module_temp(raw: int) -> float:
    """Convert raw register to module temperature (℃)"""
    return raw / 100.0 - 20.0


def raw_to_accum_radiation(raw: int) -> float:
    """Convert raw register to cumulative radiation (MJ/m²)"""
    return raw / 1000.0


# Inverse functions for simulator
def air_temp_to_raw(temp: float) -> int:
    """Convert air temperature (℃) to raw register"""
    return int((temp + 40.0) * 100)


def humidity_to_raw(humidity: float) -> int:
    """Convert humidity (%RH) to raw register"""
    return int(humidity * 100)


def pressure_to_raw(pressure: float) -> int:
    """Convert pressure (hPa) to raw register"""
    return int(pressure * 10)


def wind_speed_to_raw(speed: float) -> int:
    """Convert wind speed (m/s) to raw register"""
    return int(speed * 10)


def wind_direction_to_raw(direction: float) -> int:
    """Convert wind direction (°) to raw register"""
    return int(direction * 10)


def module_temp_to_raw(temp: float) -> int:
    """Convert module temperature (℃) to raw register"""
    return int((temp + 20.0) * 100)


def accum_radiation_to_raw(accum: float) -> int:
    """Convert cumulative radiation (MJ/m²) to raw register"""
    return int(accum * 1000)


# H01 Body field mapping (Modbus register order)
H01_WEATHER_FIELD_MAP = {
    'air_temp': {'offset': 0, 'length': 2, 'scale': 10},        # ℃ x10
    'air_humidity': {'offset': 2, 'length': 2, 'scale': 10},    # % x10
    'air_pressure': {'offset': 4, 'length': 2, 'scale': 10},    # hPa x10
    'wind_speed': {'offset': 6, 'length': 2, 'scale': 10},      # m/s x10
    'wind_direction': {'offset': 8, 'length': 2, 'scale': 1},   # °
    'module_temp_1': {'offset': 10, 'length': 2, 'scale': 10},  # ℃ x10
    'horizontal_radiation': {'offset': 12, 'length': 2, 'scale': 1},  # W/m²
    'horizontal_accum': {'offset': 14, 'length': 2, 'scale': 100},    # MJ/m² x100
    'inclined_radiation': {'offset': 16, 'length': 2, 'scale': 1},    # W/m²
    'inclined_accum': {'offset': 18, 'length': 2, 'scale': 100},      # MJ/m² x100
    'module_temp_2': {'offset': 20, 'length': 2, 'scale': 10},  # ℃ x10
    'module_temp_3': {'offset': 22, 'length': 2, 'scale': 10},  # ℃ x10
    'module_temp_4': {'offset': 24, 'length': 2, 'scale': 10},  # ℃ x10
}

H01_WEATHER_BODY_SIZE = 26  # bytes
