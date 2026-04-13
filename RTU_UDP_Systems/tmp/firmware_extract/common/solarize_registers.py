"""
Solarize Inverter Modbus Register Map
Protocol: Solarize (VerterKing, VK50, etc.)

This module re-exports from solarize_mm_registers (Model Maker generated).
Preserved as the canonical import target for all RTU and simulator code.
"""

from common.solarize_mm_registers import (
    RegisterMap,
    IVScanCommand,
    IVScanStatus,
    InverterMode,
    DerActionMode,
    DeviceType,
    ControlMode,
    IVScanBodyType,
    ErrorCode1,
    ErrorCode2,
    ErrorCode3,
    SCALE,
    registers_to_u32,
    registers_to_s32,
    registers_to_u32_be,
    registers_to_s32_be,
    get_string_registers,
    get_mppt_registers,
    get_iv_tracker_voltage_registers,
    get_iv_string_current_registers,
    get_iv_string_mapping,
    generate_iv_voltage_data,
    generate_iv_current_data,
    SolarizeStatusConverter,
    StatusConverter,
    DATA_TYPES,
    FLOAT32_FIELDS,
)
