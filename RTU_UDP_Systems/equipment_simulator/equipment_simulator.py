#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Equipment Modbus Simulator (Multi-Slave)
Runs on PC with RS485 adapter - Single COM port, Multiple Slave IDs
Version: 1.4.0

Supported Devices:
- Inverter (Solarize): Slave ID 1, Holding Register (FC03)
- Protection Relay (KDU-300): Slave ID 2, Holding Register (FC03)
- Weather Station (SEM5046): Slave ID 3, Holding Register (FC03)
- Inverter (Kstar KSG-60KT-M1): Slave ID 4, Input Register (FC04)
- Inverter (Huawei SUN2000-50KTL): Slave ID 5, Holding Register (FC03)

Version 1.3.0:
- Added Huawei SUN2000-50KTL inverter simulator (Slave ID 5, FC03)

Version 1.2.8:
- Added register name display in Modbus log (e.g., 0x07D1 DER_ACTION_MODE)

Version 1.2.7:
- Fixed infinite recursion: set _internal_update flag before calling _update_registers()
- Set _internal_update during initialization to prevent control register callbacks

Version 1.2.6:
- Fixed Modbus log address display (shows actual register address, not internal offset)
- Log all external READ/WRITE operations for debugging

Version 1.2.5:
- Fixed pymodbus 3.7+ address offset issue using store.setValues()/getValues()
- Removed ADDR_OFFSET workaround, now uses proper Modbus API

Version 1.2.4:
- Fixed ModbusServerContext parameter (slaves -> devices for pymodbus 3.7+)

Version 1.2.3:
- Removed zero_mode parameter (deprecated in pymodbus 3.7+)

Version 1.2.2:
- Fixed pymodbus 3.7+ compatibility (ModbusSlaveContext -> ModbusDeviceContext)
- Fixed ModbusDeviceIdentification import path

Version 1.2.1:
- Fixed pymodbus import error handling (sys.exit on failure)

Version 1.2.0:
- Added SEM5046 weather station simulator
- Time-based solar radiation pattern (sunrise to sunset)
- Module temperature with variance

Version 1.1.0:
- Multi-device support on single COM port
- Inverter (Solarize) + Protection Relay (KDU-300) combined
- Unified Modbus server with multiple slave contexts
- Combined status display

Version 1.0.0:
- Initial release with KDU-300 only
"""

import sys
import os
import time
import math
import struct
import threading
import logging
import argparse
import random
import json
from datetime import datetime
from collections import deque

# 프로젝트 루트: 독립 패키지면 __file__ 부모, pc_programs/ 하위면 2단계 위
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = _SCRIPT_DIR if os.path.isdir(os.path.join(_SCRIPT_DIR, 'common')) \
    else os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PROJECT_ROOT)

try:
    from pymodbus.server import StartSerialServer
    from pymodbus.datastore import ModbusServerContext, ModbusSequentialDataBlock
    from pymodbus import ModbusDeviceIdentification
    # pymodbus 3.7+ renamed ModbusSlaveContext to ModbusDeviceContext
    try:
        from pymodbus.datastore import ModbusSlaveContext
    except ImportError:
        from pymodbus.datastore import ModbusDeviceContext as ModbusSlaveContext
except ImportError as e:
    print(f"ERROR: pymodbus import failed: {e}")
    print("Install: pip install pymodbus")
    sys.exit(1)

from common.Solarize_PV_50kw_registers import (
    RegisterMap, InverterMode, SCALE,
    generate_iv_voltage_data, generate_iv_current_data,
    get_iv_tracker_voltage_registers, get_iv_string_current_registers
)

# IV Scan 상수 — 레지스터 파일에 없을 수 있으므로 시뮬레이터 자체 정의
try:
    from common.Solarize_PV_50kw_registers import IVScanCommand, IVScanStatus
except ImportError:
    class IVScanCommand:
        NON_ACTIVE = 0
        ACTIVE = 1

    class IVScanStatus:
        IDLE = 0
        RUNNING = 1
        FINISHED = 2
        @classmethod
        def to_string(cls, v):
            return {0: 'IDLE', 1: 'RUNNING', 2: 'FINISHED'}.get(v, f'UNKNOWN({v})')
from common.REF_relay_registers import KDU300RegisterMap, float_to_registers
from common.REF_weather_registers import (
    SEM5046RegisterMap,
    air_temp_to_raw, humidity_to_raw, pressure_to_raw,
    wind_speed_to_raw, wind_direction_to_raw, module_temp_to_raw,
    accum_radiation_to_raw
)
from common.Kstar_PV_60kw_registers import RegisterMap as KstarRegisters
from common.Huawei_PV_50kw_registers import RegisterMap as HuaweiRegisters
try:
    from common.Huawei_PV_50kw_registers import HuaweiStatusConverter
except ImportError:
    from common.Huawei_PV_50kw_registers import HuaweiPvStatusConverter as HuaweiStatusConverter
try:
    from common.Ekos_PV_10kw_registers import RegisterMap as EkosRegisters, InverterMode as EkosInverterMode
except ImportError:
    EkosRegisters = None
    EkosInverterMode = None
try:
    from common.Sungrow_PV_50kw_registers import RegisterMap as SungrowRegisters, InverterMode as SungrowInverterMode
except ImportError:
    SungrowRegisters = None
    SungrowInverterMode = None


# =============================================================================
# Modbus Data Blocks with Logging
# =============================================================================

# Register address to name mapping for logging
REGISTER_NAMES = {
    # Device Info
    0x1A00: "DEVICE_MODEL",
    0x1A10: "SERIAL_NUMBER",
    0x1A1C: "MASTER_FW_VER",
    0x1A26: "SLAVE_FW_VER",
    0x1A3B: "MPPT_COUNT",
    0x1A44: "NOMINAL_VOLTAGE",
    0x1A45: "NOMINAL_FREQ",
    0x1A46: "NOMINAL_POWER_L",
    0x1A4E: "NOMINAL_POWER_H",
    0x1A48: "GRID_PHASE_NUM",
    0x1A60: "EMS_FW_VER",
    0x1A8E: "LCD_FW_VER",
    # Status
    0x101C: "INNER_TEMP",
    0x101D: "INVERTER_MODE",
    0x1021: "TOTAL_ENERGY_L",
    0x1022: "TOTAL_ENERGY_H",
    0x1027: "TODAY_ENERGY_L",
    0x1028: "TODAY_ENERGY_H",
    0x1037: "GRID_ACTIVE_PWR_L",
    0x1038: "GRID_ACTIVE_PWR_H",
    0x1039: "GRID_REACTIVE_L",
    0x103A: "GRID_REACTIVE_H",
    0x103D: "POWER_FACTOR",
    0x1048: "PV_INPUT_PWR_L",
    0x1049: "PV_INPUT_PWR_H",
    # DEA
    0x03F4: "DEA_ACTIVE_PWR_L",
    0x03F5: "DEA_ACTIVE_PWR_H",
    0x03F6: "DEA_REACTIVE_L",
    0x03F7: "DEA_REACTIVE_H",
    0x03F8: "DEA_PF_L",
    0x03F9: "DEA_PF_H",
    0x03FA: "DEA_FREQ_L",
    0x03FB: "DEA_FREQ_H",
    0x03FC: "DEA_STATUS_L",
    0x03FD: "DEA_STATUS_H",
    # DER Control
    0x07D0: "DER_PF_SET",
    0x07D1: "DER_ACTION_MODE",
    0x07D2: "DER_REACTIVE_PCT",
    0x07D3: "DER_ACTIVE_PCT",
    0x0834: "INVERTER_ON_OFF",
    # Commands
    0x6001: "INVERTER_CTRL",
    0x600D: "IV_CURVE_SCAN",
    0x600F: "PF_DYNAMIC",
    0x6010: "REACTIVE_DYNAMIC",
    0x3005: "POWER_DERATING",
    # IV Scan Status
    0x8000: "IV_SCAN_STATUS",
    0x8040: "IV_STRING1",
    0x8080: "IV_STRING2",
    0x8180: "IV_STRING3",
    0x81C0: "IV_STRING4",
    0x82C0: "IV_STRING5",
    0x8300: "IV_STRING6",
    0x8400: "IV_STRING7",
    0x8440: "IV_STRING8",
}

def get_register_name(addr):
    """Get register name for address, checking range for multi-register blocks"""
    if addr in REGISTER_NAMES:
        return REGISTER_NAMES[addr]
    # Check IV string ranges
    for base in [0x8040, 0x8080, 0x8180, 0x81C0, 0x82C0, 0x8300, 0x8400, 0x8440]:
        if base <= addr < base + 64:
            return f"IV_STR{([0x8040,0x8080,0x8180,0x81C0,0x82C0,0x8300,0x8400,0x8440].index(base)+1)}+{addr-base}"
    return ""

class ModbusLoggedHoldingBlock(ModbusSequentialDataBlock):
    """Modbus Holding Register Block with logging"""
    
    CONTROL_REGISTERS = [0x0834, 0x07D0, 0x07D1, 0x07D2, 0x07D3, 0x600D]
    IV_STRING_BASES = [0x8040, 0x8080, 0x8180, 0x81C0, 0x82C0, 0x8300, 0x8400, 0x8440]
    
    def __init__(self, address, values, logger=None, simulator=None, name="HR"):
        super().__init__(address, values)
        self.logger = logger
        self.log_queue = deque(maxlen=100)
        self._internal_update = False
        self._update_lock = threading.Lock()
        self.simulator = simulator
        self.name = name  # "INV" or "RLY" or "WTH"
    
    def getValues(self, address, count=1):
        result = super().getValues(address, count)
        # pymodbus 3.7+ internal offset: actual Modbus address = address - 1
        actual_addr = address - 1
        
        # Log all external reads (not internal updates)
        if self.logger and not self._internal_update:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            reg_name = get_register_name(actual_addr)
            name_str = f" {reg_name}" if reg_name else ""
            log_entry = f"[{timestamp}] {self.name} READ  0x{actual_addr:04X} x{count}{name_str}"
            self.log_queue.append(log_entry)
        
        # IV Scan tracking - only for InverterSimulator
        if self.simulator and hasattr(self.simulator, 'iv_scan_status'):
            if self.simulator.iv_scan_status == IVScanStatus.FINISHED:
                for i, base_addr in enumerate(self.IV_STRING_BASES):
                    if base_addr <= actual_addr < base_addr + 64:
                        self.simulator._iv_strings_read.add(i)
                        if len(self.simulator._iv_strings_read) >= 8:
                            self.simulator.iv_scan_status = IVScanStatus.IDLE
                            self.simulator._iv_strings_read.clear()
                        break
        return result
    
    def setValues(self, address, values):
        # pymodbus 3.7+ internal offset: actual Modbus address = address - 1
        actual_addr = address - 1
        is_control_write = actual_addr in self.CONTROL_REGISTERS and not self._internal_update
        
        # Log all external writes (not internal updates)
        if self.logger and isinstance(values, list) and not self._internal_update:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            reg_name = get_register_name(actual_addr)
            name_str = f" {reg_name}" if reg_name else ""
            if len(values) <= 10:
                log_entry = f"[{timestamp}] {self.name} WRITE 0x{actual_addr:04X} <- {values}{name_str}"
            else:
                log_entry = f"[{timestamp}] {self.name} WRITE 0x{actual_addr:04X} <- [{len(values)} regs]{name_str}"
            self.log_queue.append(log_entry)
        
        result = super().setValues(address, values)
        
        if is_control_write and self.simulator:
            # Prevent recursion: set flag before calling update methods
            with self._update_lock:
                self._internal_update = True
                try:
                    self.simulator._check_control_changes()
                    self.simulator._update_registers()
                finally:
                    self._internal_update = False
        
        return result


class ModbusLoggedInputBlock(ModbusSequentialDataBlock):
    """Modbus Input Register Block with logging for Relay"""
    
    def __init__(self, address, values, logger=None, simulator=None, name="IR"):
        super().__init__(address, values)
        self.logger = logger
        self.log_queue = deque(maxlen=100)
        self._internal_update = False
        self.simulator = simulator
        self.name = name
    
    def getValues(self, address, count=1):
        result = super().getValues(address, count)
        # pymodbus 3.7+ internal offset: actual Modbus address = address - 1
        actual_addr = address - 1
        if self.logger and not self._internal_update:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            log_entry = f"[{timestamp}] {self.name} READ  0x{actual_addr:04X} x{count}"
            self.log_queue.append(log_entry)
        return result
    
    def setValues(self, address, values):
        # pymodbus 3.7+ internal offset: actual Modbus address = address - 1
        actual_addr = address - 1
        if self.logger and isinstance(values, list) and not self._internal_update:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            if len(values) <= 10:
                log_entry = f"[{timestamp}] {self.name} WRITE 0x{actual_addr:04X} <- {values}"
            else:
                log_entry = f"[{timestamp}] {self.name} WRITE 0x{actual_addr:04X} <- [{len(values)} regs]"
            self.log_queue.append(log_entry)
        return super().setValues(address, values)


# =============================================================================
# Inverter Simulator (Solarize)
# =============================================================================

class InverterSimulator:
    """Solarize Inverter Modbus Simulator - Slave ID 1"""
    
    VERSION = "1.1.0"
    MODEL_NAME = "SRPV-3-50-KS"
    SERIAL_NUMBER = "SRZ2024001234"
    FIRMWARE_VERSION = "V2.1.5"
    NOMINAL_POWER = 50000  # 50kW
    IV_SCAN_DURATION = 5.0
    
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("InvSim")
        self.running = False
        
        # Simulation state
        self.start_time = time.time()
        self.total_energy = 1000.0
        self.today_energy = 0.0
        self.mode = InverterMode.ON_GRID
        
        # Control states
        self.on_off = 0
        self.power_limit = 1000
        self.power_factor_set = 1000
        self.reactive_power_set = 0
        self.control_mode = 'PF'
        self.operation_mode = 0
        
        # IV Scan state
        self.iv_scan_status = IVScanStatus.IDLE
        self.iv_scan_start_time = 0
        self.iv_scan_data_points = 64
        self._iv_strings_read = set()
        
        # MPPT/String configuration
        self.mppt_count = 4
        self.string_count = 8
        self.strings_per_mppt = 2
        self.tracker_voc = [450.0, 448.0, 452.0, 449.0]
        self.tracker_v_min = [200.0, 200.0, 200.0, 200.0]
        self.string_isc = [10.5, 10.3, 10.6, 10.4, 10.5, 10.2, 10.7, 10.3]
        
        # Create datastore
        self.store = self._create_datastore()
        
        # Current values for display
        self._current = {}
        self._current_lock = threading.Lock()

    def _create_datastore(self):
        """Create Modbus datastore"""
        hr_block = ModbusLoggedHoldingBlock(0, [0] * 0x8500, self.logger, simulator=self, name="INV")
        
        store = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0] * 100),
            co=ModbusSequentialDataBlock(0, [0] * 100),
            hr=hr_block,
            ir=ModbusSequentialDataBlock(0, [0] * 100)
        )
        
        # Assign store first so init methods can use it
        self.store = store
        
        # Set internal update flag to prevent recursion during init
        hr_block._internal_update = True
        self._init_device_info()
        self._init_iv_scan_registers()
        hr_block._internal_update = False
        
        return store
    
    def _init_device_info(self):
        """Initialize device information registers using store.setValues()"""
        # Model name (16 registers = 32 bytes, matching RTU read_model_info)
        model_bytes = self.MODEL_NAME.encode('utf-8').ljust(32, b'\x00')
        model_regs = [(model_bytes[i*2] << 8) | model_bytes[i*2+1] for i in range(16)]
        self.store.setValues(3, RegisterMap.DEVICE_MODEL_NAME, model_regs)

        # Serial number (8 registers = 16 bytes)
        serial_bytes = self.SERIAL_NUMBER.encode('utf-8').ljust(16, b'\x00')
        serial_regs = [(serial_bytes[i*2] << 8) | serial_bytes[i*2+1] for i in range(8)]
        self.store.setValues(3, RegisterMap.DEVICE_SERIAL_NUMBER, serial_regs)
        
        # Firmware version (3 registers)
        fw_bytes = self.FIRMWARE_VERSION.encode('utf-8').ljust(6, b'\x00')
        fw_regs = [(fw_bytes[i*2] << 8) | fw_bytes[i*2+1] for i in range(3)]
        self.store.setValues(3, RegisterMap.FIRMWARE_VERSION, fw_regs)
        
        # Device info registers
        self.store.setValues(3, RegisterMap.MPPT_NUMBER, [self.mppt_count])
        self.store.setValues(3, RegisterMap.NOMINAL_ACTIVE_POWER_LOW_WORD, [self.NOMINAL_POWER & 0xFFFF])
        self.store.setValues(3, RegisterMap.NOMINAL_ACTIVE_POWER_HIGH_WORD, [(self.NOMINAL_POWER >> 16) & 0xFFFF])
        self.store.setValues(3, RegisterMap.GRID_PHASE_NUMBER, [3])
        self.store.setValues(3, RegisterMap.NOMINAL_VOLTAGE, [3800])
        self.store.setValues(3, RegisterMap.NOMINAL_FREQUENCY, [6000])
        
        # Control registers
        self.store.setValues(3, RegisterMap.INVERTER_ON_OFF, [self.on_off])
        self.store.setValues(3, RegisterMap.DER_ACTIVE_POWER_PCT, [self.power_limit])
        self.store.setValues(3, RegisterMap.DER_POWER_FACTOR_SET, [self.power_factor_set])
        self.store.setValues(3, RegisterMap.DER_REACTIVE_POWER_PCT, [self.reactive_power_set])
        self.store.setValues(3, RegisterMap.DER_ACTION_MODE, [self.operation_mode])
        self.store.setValues(3, 0x600D, [IVScanStatus.IDLE])
        
        # DEA registers
        self.store.setValues(3, 0x03F9, [1000])   # DEA_POWER_FACTOR low word
        self.store.setValues(3, 0x03FB, [600])    # DEA_FREQUENCY low word
        self.store.setValues(3, RegisterMap.DER_AVM_DIGITAL_METERCONNECT_STATUS + 1, [0x0001])
    
    def _init_iv_scan_registers(self):
        """Initialize IV Scan data registers using store.setValues()"""
        for mppt in range(1, self.mppt_count + 1):
            voc = self.tracker_voc[mppt - 1]
            v_min = self.tracker_v_min[mppt - 1]
            
            v_regs = get_iv_tracker_voltage_registers(mppt, self.iv_scan_data_points)
            voltages = generate_iv_voltage_data(voc, v_min, self.iv_scan_data_points)
            self.store.setValues(3, v_regs['base'], voltages)
            
            for string in range(1, self.strings_per_mppt + 1):
                string_idx = (mppt - 1) * self.strings_per_mppt + (string - 1)
                isc = self.string_isc[string_idx]
                i_regs = get_iv_string_current_registers(mppt, string, self.iv_scan_data_points)
                currents = generate_iv_current_data(isc, voc, v_min, self.iv_scan_data_points)
                self.store.setValues(3, i_regs['base'], currents)
    
    def _get_sun_factor(self):
        """Get sun intensity based on time"""
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        
        if hour < 5.0 or hour >= 19.0:
            return 0.0
        if hour < 6.0:
            return (hour - 5.0) * 0.1
        if hour < 12.0:
            return 0.1 + (hour - 6.0) / 6.0 * 0.9
        if hour < 18.0:
            return 1.0 - (hour - 12.0) / 6.0 * 0.9
        return (19.0 - hour) * 0.1
    
    def _update_registers(self):
        """Update register values using store.setValues()"""
        sun_factor = self._get_sun_factor()
        
        if self.on_off == 1 or self.mode != InverterMode.ON_GRID:
            sun_factor = 0
        
        power_cap = self.NOMINAL_POWER * (self.power_limit / 1000.0)
        EFFICIENCY = 0.975

        # AC 출력 먼저 결정 → PV 역산 (효율 97.5%)
        if sun_factor > 0:
            possible_ac_w = sun_factor * self.NOMINAL_POWER * EFFICIENCY
            ac_power_w = min(possible_ac_w, power_cap)
            pv_power_w = ac_power_w / EFFICIENCY
            pv_voltage = int(300 + sun_factor * 180)
            pv_current_a = pv_power_w / pv_voltage if pv_voltage > 0 else 0
        else:
            ac_power_w = 0
            pv_power_w = 0
            pv_voltage = 0
            pv_current_a = 0

        pv_power = int(pv_power_w * 10)
        
        if self.control_mode == 'PF':
            pf = self.power_factor_set / 1000.0
            pf = max(0.85, min(1.0, abs(pf)))
            if ac_power_w > 0 and pf < 1.0:
                reactive_power_w = ac_power_w * math.tan(math.acos(pf))
                if self.power_factor_set < 0:
                    reactive_power_w = -reactive_power_w
            else:
                reactive_power_w = 0
        else:
            rp_pct = self.reactive_power_set
            if rp_pct >= 32768:
                rp_pct = rp_pct - 65536
            reactive_power_w = self.NOMINAL_POWER * (rp_pct / 1000.0)
            if ac_power_w > 0:
                apparent = math.sqrt(ac_power_w**2 + reactive_power_w**2)
                pf = ac_power_w / apparent if apparent > 0 else 1.0
            else:
                pf = 1.0
        
        ac_power = int(ac_power_w * 10)
        ac_voltage = 3800
        ac_freq = 6000
        phase_power = ac_power // 3
        
        apparent_power = math.sqrt(ac_power_w**2 + reactive_power_w**2) if ac_power_w > 0 else 0
        # 3상 전류 계산: I = S / (sqrt(3) * V_line). 단위: 0.01A
        # apparent_power/3 → 1상 피상전력(VA), /380(V) → 전류(A), *100 → 0.01A 단위
        phase_current = int((apparent_power / math.sqrt(3)) / 380 * 100) if apparent_power > 0 else 0
        
        if ac_power > 0:
            self.total_energy += (ac_power / 10) / 3600000  # Wh → kWh (누적, kWh)
            self.today_energy += (ac_power / 10) / 3600     # Wh/s → Wh (당일, Wh 단위. 레지스터 기록 시 /10 → 0.1kWh)
        
        # Phase data (L1, L2, L3)
        for base in [RegisterMap.L1_VOLTAGE, RegisterMap.L2_VOLTAGE, RegisterMap.L3_VOLTAGE]:
            self.store.setValues(3, base, [ac_voltage, phase_current, phase_power & 0xFFFF, (phase_power >> 16) & 0xFFFF, ac_freq])
        
        # MPPT data
        mppt_addresses = [
            (RegisterMap.MPPT1_VOLTAGE, RegisterMap.MPPT1_CURRENT, RegisterMap.MPPT1_POWER + 1),
            (RegisterMap.MPPT2_VOLTAGE, RegisterMap.MPPT2_CURRENT, RegisterMap.MPPT2_POWER + 1),
            (RegisterMap.MPPT3_VOLTAGE, RegisterMap.MPPT3_CURRENT, RegisterMap.MPPT3_POWER + 1),
            (RegisterMap.MPPT4_VOLTAGE, RegisterMap.MPPT4_CURRENT, RegisterMap.MPPT4_POWER + 1),
        ]
        
        for i, (v_addr, c_addr, p_addr) in enumerate(mppt_addresses):
            if pv_power_w > 0:
                mppt_v = int((300 + sun_factor * 180 + i * 3) * 10)
                mppt_power_w = pv_power_w / 4
                mppt_c = int((mppt_power_w / (mppt_v / 10)) * 100)
                mppt_p = int(mppt_power_w * 10)
            else:
                mppt_v = 0
                mppt_c = 0
                mppt_p = 0
            self.store.setValues(3, v_addr, [mppt_v])
            self.store.setValues(3, c_addr, [mppt_c])
            self.store.setValues(3, p_addr, [mppt_p & 0xFFFF, (mppt_p >> 16) & 0xFFFF])
        
        # String data
        for i in range(self.string_count):
            mppt_idx = i // 2
            if pv_power_w > 0:
                str_voltage = int((300 + sun_factor * 180 + mppt_idx * 3 + (i % 2) * 2) * 10)
                # 각 스트링 전류 = MPPT 전류 / strings_per_mppt
                mppt_current_a = (pv_power_w / 4) / (str_voltage / 10) if str_voltage > 0 else 0
                str_current = int((mppt_current_a / self.strings_per_mppt + (i % 2) * 0.3) * 100)
            else:
                str_voltage = 0
                str_current = 0
            base_addr = RegisterMap.STRING1_VOLTAGE + i * 2
            self.store.setValues(3, base_addr, [str_voltage, str_current])
        
        # Power registers
        self.store.setValues(3, RegisterMap.PV_POWER + 1, [pv_power & 0xFFFF, (pv_power >> 16) & 0xFFFF])
        self.store.setValues(3, RegisterMap.AC_POWER + 1, [ac_power & 0xFFFF, (ac_power >> 16) & 0xFFFF])
        
        # Mode and status
        mode_val = InverterMode.STANDBY if self.on_off == 1 else self.mode
        self.store.setValues(3, RegisterMap.INVERTER_MODE, [mode_val])
        self.store.setValues(3, RegisterMap.INNER_TEMP, [45])
        self.store.setValues(3, RegisterMap.ERROR_CODE1, [0, 0, 0])  # ERROR_CODE1, 2, 3
        
        # Energy registers
        total_kwh = int(self.total_energy)
        self.store.setValues(3, RegisterMap.CUMULATIVE_ENERGY_LOW, [total_kwh & 0xFFFF, (total_kwh >> 16) & 0xFFFF])
        
        # Power factor
        pf_reg = int(pf * 1000)
        self.store.setValues(3, RegisterMap.POWER_FACTOR, [pf_reg & 0xFFFF])
        
        # DEA-AVM registers
        phase_current_dea = int(phase_current / 10)
        active_power_dea = int(ac_power / 1000)
        reactive_power_dea = int(reactive_power_w)
        frequency_dea = int(ac_freq / 10)
        status_flags = 0x0001 if self.on_off == 0 else 0x0000
        
        def to_u32(val):
            if val < 0:
                val = val + 0x100000000
            return val
        
        active_u32 = to_u32(active_power_dea)
        reactive_u32 = to_u32(reactive_power_dea)
        pf_u32 = to_u32(pf_reg)
        
        dea_values = [
            phase_current_dea & 0xFFFF, (phase_current_dea >> 16) & 0xFFFF,
            phase_current_dea & 0xFFFF, (phase_current_dea >> 16) & 0xFFFF,
            phase_current_dea & 0xFFFF, (phase_current_dea >> 16) & 0xFFFF,
            ac_voltage & 0xFFFF, (ac_voltage >> 16) & 0xFFFF,
            ac_voltage & 0xFFFF, (ac_voltage >> 16) & 0xFFFF,
            ac_voltage & 0xFFFF, (ac_voltage >> 16) & 0xFFFF,
            active_u32 & 0xFFFF, (active_u32 >> 16) & 0xFFFF,
            reactive_u32 & 0xFFFF, (reactive_u32 >> 16) & 0xFFFF,
            pf_u32 & 0xFFFF, (pf_u32 >> 16) & 0xFFFF,
            frequency_dea & 0xFFFF, (frequency_dea >> 16) & 0xFFFF,
            status_flags, 0,
        ]
        self.store.setValues(3, 0x03E9, dea_values)  # DEA_L1_CURRENT low word
        
        # IV Scan status
        if self.iv_scan_status == IVScanStatus.RUNNING:
            elapsed_scan = time.time() - self.iv_scan_start_time
            if elapsed_scan >= self.IV_SCAN_DURATION:
                self.iv_scan_status = IVScanStatus.FINISHED
                self._regenerate_iv_data()
        elif self.iv_scan_status == IVScanStatus.FINISHED:
            # Auto-reset to IDLE after 60s if strings not fully read
            if time.time() - self.iv_scan_start_time > self.IV_SCAN_DURATION + 60:
                self.iv_scan_status = IVScanStatus.IDLE
                self._iv_strings_read.clear()
        
        self.store.setValues(3, 0x600D, [self.iv_scan_status])
        
        # Store for display
        self._current = {
            'sun_factor': sun_factor,
            'pv_power_kw': pv_power_w / 1000,
            'ac_power_kw': ac_power_w / 1000,
            'reactive_kvar': reactive_power_w / 1000,
            'voltage': ac_voltage / 10,
            'freq': ac_freq / 100,
            'pf': pf,
            'on_off': 'ON' if self.on_off == 0 else 'OFF',
            'mode': self.mode,
            'ctrl_mode': self.control_mode,
            'power_limit': self.power_limit,
        }
    
    def _regenerate_iv_data(self):
        """Regenerate IV Scan data using store.setValues()"""
        for mppt in range(1, self.mppt_count + 1):
            voc = self.tracker_voc[mppt - 1] + (time.time() % 5) - 2.5
            v_min = self.tracker_v_min[mppt - 1]
            
            v_regs = get_iv_tracker_voltage_registers(mppt, self.iv_scan_data_points)
            voltages = generate_iv_voltage_data(voc, v_min, self.iv_scan_data_points)
            self.store.setValues(3, v_regs['base'], voltages)
            
            for string in range(1, self.strings_per_mppt + 1):
                string_idx = (mppt - 1) * self.strings_per_mppt + (string - 1)
                isc = self.string_isc[string_idx] + (time.time() % 3) * 0.1 - 0.15
                i_regs = get_iv_string_current_registers(mppt, string, self.iv_scan_data_points)
                currents = generate_iv_current_data(isc, voc, v_min, self.iv_scan_data_points)
                self.store.setValues(3, i_regs['base'], currents)
    
    def _check_control_changes(self):
        """Check for control register changes using store.getValues()"""
        new_onoff = self.store.getValues(3, RegisterMap.INVERTER_ON_OFF, count=1)[0]
        if new_onoff != self.on_off and new_onoff in [0, 1]:
            self.on_off = new_onoff
            self.mode = InverterMode.ON_GRID if self.on_off == 0 else InverterMode.SHUTDOWN
        
        new_power = self.store.getValues(3, RegisterMap.DER_ACTIVE_POWER_PCT, count=1)[0]
        if new_power != self.power_limit and 0 <= new_power <= 1100:
            self.power_limit = new_power
        
        new_pf = self.store.getValues(3, RegisterMap.DER_POWER_FACTOR_SET, count=1)[0]
        if new_pf != self.power_factor_set:
            self.power_factor_set = new_pf
            self.control_mode = 'PF'
        
        new_rp = self.store.getValues(3, RegisterMap.DER_REACTIVE_POWER_PCT, count=1)[0]
        if new_rp != self.reactive_power_set:
            self.reactive_power_set = new_rp
            self.control_mode = 'RP'
        
        new_mode = self.store.getValues(3, RegisterMap.DER_ACTION_MODE, count=1)[0]
        if new_mode != self.operation_mode:
            self.operation_mode = new_mode
        
        iv_cmd = self.store.getValues(3, 0x600D, count=1)[0]
        if iv_cmd == IVScanCommand.ACTIVE and self.iv_scan_status in [IVScanStatus.IDLE, IVScanStatus.FINISHED]:
            self.iv_scan_status = IVScanStatus.RUNNING
            self.iv_scan_start_time = time.time()
            self._iv_strings_read.clear()
            self.store.setValues(3, 0x600D, [IVScanStatus.RUNNING])


# =============================================================================
# Relay Simulator (KDU-300)
# =============================================================================

class RelaySimulator:
    """KDU-300 Protection Relay Simulator - Slave ID 2
    
    24-hour factory load pattern (100kW base)
    Uses Holding Register (FC03) for simulator compatibility
    """
    
    VERSION = "1.1.0"
    NOMINAL_LINE_VOLTAGE = 380.0
    NOMINAL_PHASE_VOLTAGE = 220.0
    NOMINAL_FREQUENCY = 60.0
    NOMINAL_POWER = 100000.0
    NOMINAL_POWER_FACTOR = 0.90
    
    def __init__(self, logger=None, inverter_sims=None):
        self.logger = logger or logging.getLogger("RelaySim")
        self.running = False
        self.inverter_sims = inverter_sims or []  # Reference to inverter simulators

        self.start_time = time.time()
        self.received_energy_wh = 50000.0   # 수전유효전력량 (+WH)
        self.sent_energy_wh = 0.0           # 송전유효전력량 (-WH)
        self.total_energy_varh = 20000.0
        
        self.max_values = {
            'v12': 0.0, 'v23': 0.0, 'v31': 0.0,
            'v1': 0.0, 'v2': 0.0, 'v3': 0.0,
            'a1': 0.0, 'a2': 0.0, 'a3': 0.0,
            'w': 0.0
        }
        
        self.store = self._create_datastore()
        self._current = {}
    
    def _create_datastore(self):
        """Create Modbus datastore - Uses Holding Register (FC03)"""
        hr_block = ModbusLoggedHoldingBlock(
            0, [0] * KDU300RegisterMap.TOTAL_REGISTERS,
            self.logger, simulator=self, name="RLY"
        )
        
        store = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0] * 100),
            co=ModbusSequentialDataBlock(0, [0] * 100),
            hr=hr_block,
            ir=ModbusSequentialDataBlock(0, [0] * 100)
        )
        
        return store
    
    def _get_total_inverter_power_w(self):
        """Get total AC power from all connected inverters (W)"""
        total = 0.0
        for sim in self.inverter_sims:
            if hasattr(sim, '_current') and sim._current:
                total += sim._current.get('ac_power_kw', 0.0) * 1000.0
        return total

    def _get_load_power_w(self, inverter_power_w):
        """Get factory load power (W) — peak is ~2x inverter generation

        Factory load pattern based on time of day:
        - 06~09: Morning ramp-up (0.5~1.5x inverter power)
        - 09~12: Morning peak (1.5~2.0x)
        - 12~13: Lunch dip (1.0x)
        - 13~18: Afternoon peak (1.5~2.0x)
        - 18~22: Evening decline (0.8~0.3x)
        - 22~06: Night base load (0.2~0.1x)
        """
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        fluctuation = random.uniform(-0.05, 0.05)

        # Base multiplier relative to inverter power
        if hour < 6.0:
            mult = 0.15
        elif hour < 9.0:
            mult = 0.5 + (hour - 6.0) / 3.0 * 1.0  # 0.5 → 1.5
        elif hour < 12.0:
            mult = 1.5 + 0.5 * math.sin((hour - 9.0) / 3.0 * math.pi)  # 1.5 → 2.0 → 1.5
        elif hour < 13.0:
            mult = 1.0  # Lunch dip
        elif hour < 18.0:
            mult = 1.5 + 0.5 * math.sin((hour - 13.0) / 5.0 * math.pi)  # 1.5 → 2.0 → 1.5
        elif hour < 22.0:
            mult = 0.8 - (hour - 18.0) / 4.0 * 0.5  # 0.8 → 0.3
        else:
            mult = 0.2 - (hour - 22.0) / 2.0 * 0.1  # 0.2 → 0.1

        mult = max(0.1, mult + fluctuation)

        # Use inverter power as reference, with minimum base load of 30kW
        ref_power = max(inverter_power_w, 50000.0)
        return ref_power * mult
    
    def _update_registers(self):
        """Update all register values — PCC net power model"""
        # Get inverter generation and factory load
        inverter_power_w = self._get_total_inverter_power_w()
        load_power_w = self._get_load_power_w(inverter_power_w)

        # Net power at PCC = Load - Inverter Generation
        # Positive = consuming from grid, Negative = exporting to grid
        net_power_w = load_power_w - inverter_power_w

        pf = self.NOMINAL_POWER_FACTOR + random.uniform(-0.03, 0.03)

        # Voltage
        v_base = self.NOMINAL_LINE_VOLTAGE * (1.0 + random.uniform(-0.02, 0.02))
        v12 = v_base * (1.0 + random.uniform(-0.01, 0.01))
        v23 = v_base * (1.0 + random.uniform(-0.01, 0.01))
        v31 = v_base * (1.0 + random.uniform(-0.01, 0.01))

        v1 = v12 / math.sqrt(3) * (1.0 + random.uniform(-0.005, 0.005))
        v2 = v23 / math.sqrt(3) * (1.0 + random.uniform(-0.005, 0.005))
        v3 = v31 / math.sqrt(3) * (1.0 + random.uniform(-0.005, 0.005))

        freq = self.NOMINAL_FREQUENCY + random.uniform(-0.1, 0.1)

        # Power — net_power_w can be negative (export to grid)
        total_w = net_power_w
        w1 = total_w / 3 * (1.0 + random.uniform(-0.03, 0.03))
        w2 = total_w / 3 * (1.0 + random.uniform(-0.03, 0.03))
        w3 = total_w - w1 - w2
        
        pf_angle = math.acos(pf)
        total_reactive = total_w * math.tan(pf_angle)
        var1 = total_reactive / 3 * (1.0 + random.uniform(-0.03, 0.03))
        var2 = total_reactive / 3 * (1.0 + random.uniform(-0.03, 0.03))
        var3 = total_reactive - var1 - var2
        total_var = var1 + var2 + var3
        
        va1 = math.sqrt(w1**2 + var1**2)
        va2 = math.sqrt(w2**2 + var2**2)
        va3 = math.sqrt(w3**2 + var3**2)
        total_va = math.sqrt(total_w**2 + total_var**2)
        
        a1 = va1 / v1 if v1 > 0 else 0
        a2 = va2 / v2 if v2 > 0 else 0
        a3 = va3 / v3 if v3 > 0 else 0
        
        pf1 = w1 / va1 if va1 > 0 else 1.0
        pf2 = w2 / va2 if va2 > 0 else 1.0
        pf3 = w3 / va3 if va3 > 0 else 1.0
        avg_pf = total_w / total_va if total_va > 0 else 1.0
        
        p1_angle = math.degrees(math.acos(min(pf1, 1.0)))
        p2_angle = math.degrees(math.acos(min(pf2, 1.0)))
        p3_angle = math.degrees(math.acos(min(pf3, 1.0)))
        
        # Energy accumulation based on power direction
        energy_delta = abs(total_w) * (1.0 / 3600.0)  # 1s update interval
        if total_w >= 0:
            self.received_energy_wh += energy_delta   # 수전 (+WH)
        else:
            self.sent_energy_wh += energy_delta        # 역송 (-WH)
        self.total_energy_varh += abs(total_var) * (1.0 / 3600.0)
        
        # Update max values
        self.max_values['v12'] = max(self.max_values['v12'], v12)
        self.max_values['v23'] = max(self.max_values['v23'], v23)
        self.max_values['v31'] = max(self.max_values['v31'], v31)
        self.max_values['v1'] = max(self.max_values['v1'], v1)
        self.max_values['v2'] = max(self.max_values['v2'], v2)
        self.max_values['v3'] = max(self.max_values['v3'], v3)
        self.max_values['a1'] = max(self.max_values['a1'], a1)
        self.max_values['a2'] = max(self.max_values['a2'], a2)
        self.max_values['a3'] = max(self.max_values['a3'], a3)
        self.max_values['w'] = max(self.max_values['w'], total_w)
        
        def set_float(addr, value):
            hi, lo = float_to_registers(value)
            self.store.setValues(3, addr, [hi, lo])
        
        # Write all float values
        set_float(KDU300RegisterMap.V12, v12)
        set_float(KDU300RegisterMap.V23, v23)
        set_float(KDU300RegisterMap.V31, v31)
        set_float(KDU300RegisterMap.V1, v1)
        set_float(KDU300RegisterMap.V2, v2)
        set_float(KDU300RegisterMap.V3, v3)
        set_float(KDU300RegisterMap.A1, a1)
        set_float(KDU300RegisterMap.A2, a2)
        set_float(KDU300RegisterMap.A3, a3)
        set_float(KDU300RegisterMap.W1, w1)
        set_float(KDU300RegisterMap.W2, w2)
        set_float(KDU300RegisterMap.W3, w3)
        set_float(KDU300RegisterMap.TOTAL_W, total_w)
        set_float(KDU300RegisterMap.VAR1, var1)
        set_float(KDU300RegisterMap.VAR2, var2)
        set_float(KDU300RegisterMap.VAR3, var3)
        set_float(KDU300RegisterMap.TOTAL_VAR, total_var)
        set_float(KDU300RegisterMap.VA1, va1)
        set_float(KDU300RegisterMap.VA2, va2)
        set_float(KDU300RegisterMap.VA3, va3)
        set_float(KDU300RegisterMap.TOTAL_VA, total_va)
        set_float(KDU300RegisterMap.PF1, pf1)
        set_float(KDU300RegisterMap.PF2, pf2)
        set_float(KDU300RegisterMap.PF3, pf3)
        set_float(KDU300RegisterMap.AVG_PF, avg_pf)
        set_float(KDU300RegisterMap.FREQUENCY, freq)
        set_float(KDU300RegisterMap.POSITIVE_WH, self.received_energy_wh)
        set_float(KDU300RegisterMap.NEGATIVE_WH, self.sent_energy_wh)
        set_float(KDU300RegisterMap.POSITIVE_VARH, self.total_energy_varh)
        set_float(KDU300RegisterMap.NEGATIVE_VARH, 0.0)
        set_float(KDU300RegisterMap.V12_MAX, self.max_values['v12'])
        set_float(KDU300RegisterMap.V23_MAX, self.max_values['v23'])
        set_float(KDU300RegisterMap.V31_MAX, self.max_values['v31'])
        set_float(KDU300RegisterMap.V1_MAX, self.max_values['v1'])
        set_float(KDU300RegisterMap.V2_MAX, self.max_values['v2'])
        set_float(KDU300RegisterMap.V3_MAX, self.max_values['v3'])
        set_float(KDU300RegisterMap.A1_MAX, self.max_values['a1'])
        set_float(KDU300RegisterMap.A2_MAX, self.max_values['a2'])
        set_float(KDU300RegisterMap.A3_MAX, self.max_values['a3'])
        set_float(KDU300RegisterMap.W_MAX, self.max_values['w'])
        set_float(KDU300RegisterMap.P1_ANGLE, p1_angle)
        set_float(KDU300RegisterMap.P2_ANGLE, p2_angle)
        set_float(KDU300RegisterMap.P3_ANGLE, p3_angle)
        set_float(KDU300RegisterMap.REVERSE_WATT1, 0.0)
        set_float(KDU300RegisterMap.REVERSE_WATT2, 0.0)
        set_float(KDU300RegisterMap.REVERSE_WATT3, 0.0)
        
        # DO status: 0x0001 if exporting to grid (reverse power), 0x0000 if consuming
        do_status = 0x0001 if total_w < 0 else 0x0000
        self.store.setValues(3, KDU300RegisterMap.DO_STATUS, [do_status])
        self.store.setValues(3, KDU300RegisterMap.OVR, [0, 0, 0, 0, 0])  # OVR, UVR, OFR, UFR, RPR
        self.store.setValues(3, KDU300RegisterMap.DI1, [do_status, 0x0000])  # DI1=역전력차단기 접점, DI2
        
        # Store for display
        self._current = {
            'inverter_kw': inverter_power_w / 1000,
            'load_kw': load_power_w / 1000,
            'net_kw': total_w / 1000,
            'total_var': total_var / 1000,
            'avg_pf': avg_pf,
            'freq': freq,
            'v12': v12,
            'a1': a1,
            'do_status': do_status,
            'received_kwh': self.received_energy_wh / 1000,
            'sent_kwh': self.sent_energy_wh / 1000,
        }


# =============================================================================
# Weather Station Simulator (SEM5046)
# =============================================================================

class WeatherSimulator:
    """SEM5046 Weather Station Simulator - Slave ID 3
    
    Time-based solar radiation pattern with realistic weather data
    """
    
    VERSION = "1.0.0"
    
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("WeatherSim")
        self.running = False
        
        self.start_time = time.time()
        self.accum_horizontal = 0.0  # MJ/m²
        self.accum_inclined = 0.0    # MJ/m²
        
        self.store = self._create_datastore()
        self._current = {}
    
    def _create_datastore(self):
        """Create Modbus datastore - Uses Holding Register (FC03)"""
        hr_block = ModbusLoggedHoldingBlock(
            0, [0] * SEM5046RegisterMap.TOTAL_REGISTERS,
            self.logger, simulator=self, name="WTH"
        )
        
        store = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0] * 100),
            co=ModbusSequentialDataBlock(0, [0] * 100),
            hr=hr_block,
            ir=ModbusSequentialDataBlock(0, [0] * 100)
        )
        
        return store
    
    def _get_solar_radiation(self):
        """Get solar radiation based on time of day (W/m²)"""
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        
        # Sunrise ~6:00, Sunset ~18:00 (simplified)
        sunrise = 6.0
        sunset = 18.0
        solar_noon = 12.0
        
        if hour < sunrise or hour > sunset:
            return 0.0
        
        # Bell curve pattern
        day_progress = (hour - sunrise) / (sunset - sunrise)
        base_radiation = 1000 * math.sin(day_progress * math.pi)
        
        # Add cloud variation
        cloud_factor = 1.0 + random.uniform(-0.15, 0.05)
        
        return max(0, base_radiation * cloud_factor)
    
    def _get_air_temperature(self):
        """Get air temperature based on time of day (℃)"""
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        
        # Min at 6:00 (~5℃), Max at 14:00 (~25℃)
        base = 15.0
        amplitude = 10.0
        phase_shift = 8.0  # Peak at 14:00
        
        temp = base + amplitude * math.sin((hour - phase_shift) / 24.0 * 2 * math.pi)
        temp += random.uniform(-1.0, 1.0)
        
        return temp
    
    def _update_registers(self):
        """Update all register values using store.setValues()"""
        # Get environmental data
        radiation = self._get_solar_radiation()
        air_temp = self._get_air_temperature()
        humidity = 50.0 + random.uniform(-15, 15)
        pressure = 1013.0 + random.uniform(-5, 5)
        wind_speed = 2.0 + random.uniform(0, 5)
        wind_direction = random.uniform(0, 360)
        
        # Module temperature (higher with more radiation)
        base_module_temp = air_temp + (radiation / 1000.0) * 30.0
        module_temp_1 = base_module_temp + random.uniform(-2, 2)
        module_temp_2 = base_module_temp + random.uniform(-2, 2)
        module_temp_3 = base_module_temp + random.uniform(-2, 2)
        module_temp_4 = base_module_temp + random.uniform(-2, 2)
        
        # Inclined radiation (tilted panel, ~15% more when sun is up)
        inclined_factor = 1.15 if radiation > 0 else 1.0
        inclined_radiation = radiation * inclined_factor
        
        # Accumulate radiation (every 2 seconds update)
        # W/m² * 2s = Ws/m² -> /1000000 = MJ/m²
        self.accum_horizontal += radiation * 2.0 / 1000000.0
        self.accum_inclined += inclined_radiation * 2.0 / 1000000.0
        
        # Write to registers using store.setValues()
        self.store.setValues(3, SEM5046RegisterMap.AIR_TEMP, [air_temp_to_raw(air_temp)])
        self.store.setValues(3, SEM5046RegisterMap.AIR_HUMIDITY, [humidity_to_raw(humidity)])
        self.store.setValues(3, SEM5046RegisterMap.AIR_PRESSURE, [pressure_to_raw(pressure)])
        self.store.setValues(3, SEM5046RegisterMap.WIND_SPEED, [wind_speed_to_raw(wind_speed)])
        self.store.setValues(3, SEM5046RegisterMap.WIND_DIRECTION, [wind_direction_to_raw(wind_direction)])
        self.store.setValues(3, SEM5046RegisterMap.MODULE_TEMP_1, [module_temp_to_raw(module_temp_1)])
        self.store.setValues(3, SEM5046RegisterMap.HORIZONTAL_RADIATION, [int(radiation)])
        self.store.setValues(3, SEM5046RegisterMap.HORIZONTAL_ACCUM, [accum_radiation_to_raw(self.accum_horizontal)])
        self.store.setValues(3, SEM5046RegisterMap.INCLINED_RADIATION, [int(inclined_radiation)])
        self.store.setValues(3, SEM5046RegisterMap.INCLINED_ACCUM, [accum_radiation_to_raw(self.accum_inclined)])
        self.store.setValues(3, SEM5046RegisterMap.MODULE_TEMP_2, [module_temp_to_raw(module_temp_2)])
        self.store.setValues(3, SEM5046RegisterMap.MODULE_TEMP_3, [module_temp_to_raw(module_temp_3)])
        self.store.setValues(3, SEM5046RegisterMap.MODULE_TEMP_4, [module_temp_to_raw(module_temp_4)])
        
        # Store for display
        self._current = {
            'radiation': radiation,
            'inclined': inclined_radiation,
            'air_temp': air_temp,
            'humidity': humidity,
            'module_temp': module_temp_1,
            'wind_speed': wind_speed,
            'accum_h': self.accum_horizontal,
            'accum_i': self.accum_inclined
        }


# =============================================================================
# Kstar Inverter Simulator (KSG-60KT-M1)
# =============================================================================

class _KstarNightOffBlock(ModbusSequentialDataBlock):
    """FC04 Input Register block that refuses reads when inverter is powered off (night)."""

    def __init__(self, address, values):
        super().__init__(address, values)
        self.night_off = False

    def getValues(self, address, count=1):
        if self.night_off:
            # Return None to trigger IllegalAddress error in pymodbus
            raise Exception("Kstar inverter powered off (night)")
        return super().getValues(address, count)


class KstarSimulator:
    """Kstar KSG-60KT-M1 Inverter Modbus Simulator - Slave ID 4

    FC04 (Input Register): 실시간 데이터 (Block1~3, Block5)
    FC03 (Holding Register): 장비 정보 (Block4)
    60kW, 3 MPPT, 9 strings (MPPT당 3개)
    """

    VERSION = "1.0.0"
    MODEL_NAME = "KSG-60KT-M1"
    NOMINAL_POWER = 60000       # 60kW
    NOMINAL_VOLTAGE = 3800      # 0.1V → 380V (line-to-line)
    NOMINAL_FREQUENCY = 6000    # 0.01Hz → 60.00Hz

    # PV MPPT nominal voltages (0.1V)
    PV_VOLTAGE_NOMINAL = [3900, 3850, 3920]  # MPPT1~3

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("KstarSim")
        self.running = False

        self.start_time = time.time()
        self.total_energy_wh = 2000000.0   # 초기 누적 발전량 2000kWh (단위: Wh)

        # DER-AVM Control states
        self.on_off = 0                # 0=ON, 1=OFF
        self.power_limit = 1000        # 0.1% units (1000 = 100%)
        self.power_factor_set = 1000   # 0.001 units (1000 = 1.0)
        self.reactive_power_set = 0    # 0.1% units
        self.control_mode = 'PF'       # 'PF' or 'RP'
        self.operation_mode = 0

        # IV Scan state
        self.iv_scan_status = 0  # 0=Idle, 1=Scanning
        self.iv_scan_start_time = 0
        self.IV_SCAN_DURATION = 5.0

        self.store = self._create_datastore()
        self._current = {}
        self._init_iv_scan_data()

    def _create_datastore(self):
        """FC04 Input Register + FC03 Holding Register 데이터스토어 생성"""
        # FC04 (ir): Block1-5 (3000~3249) + IV data (5000~7399)
        # Extra margin (7500) to avoid off-by-one in pymodbus address validation
        ir_block = _KstarNightOffBlock(0, [0] * 7500)

        # FC03 (hr): Block4(3200~3217) + DER Control(0x07D0~0x0834) + IV trigger(0x0FB3=4035)
        hr_block = ModbusLoggedHoldingBlock(
            0, [0] * 4040,
            self.logger, simulator=self, name="KST"
        )

        store = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0] * 10),
            co=ModbusSequentialDataBlock(0, [0] * 10),
            hr=hr_block,
            ir=ir_block,
        )
        self.store = store
        self.ir_block = ir_block  # FC04 Input Register 블록 직접 참조
        self._init_device_info()
        self._init_control_registers()
        return store

    def _init_control_registers(self):
        """DER-AVM 제어 레지스터 초기값 설정 (FC03 Holding Register)"""
        self.store.store['h']._internal_update = True
        try:
            self.store.setValues(3, KstarRegisters.DER_POWER_FACTOR_SET, [self.power_factor_set])
            self.store.setValues(3, KstarRegisters.DER_ACTION_MODE, [self.operation_mode])
            self.store.setValues(3, KstarRegisters.DER_REACTIVE_POWER_PCT, [self.reactive_power_set])
            self.store.setValues(3, KstarRegisters.DER_ACTIVE_POWER_PCT, [self.power_limit])
            self.store.setValues(3, KstarRegisters.INVERTER_ON_OFF, [self.on_off])
        finally:
            self.store.store['h']._internal_update = False

    def _init_device_info(self):
        """Block4 (FC03, 3200~3217) 장비 정보 초기화"""
        # MODEL_NAME_BASE = 3200, 8 regs ASCII
        model_bytes = self.MODEL_NAME.encode('ascii').ljust(16, b'\x00')
        model_regs = [(model_bytes[i * 2] << 8) | model_bytes[i * 2 + 1] for i in range(8)]
        self.store.setValues(3, KstarRegisters.MODEL_NAME_BASE, model_regs)

        # ARM_VERSION = 3216: 버전 100 (1.00)
        self.store.setValues(3, KstarRegisters.ARM_VERSION, [100])
        # DSP_VERSION = 3217: 버전 100 (1.00)
        self.store.setValues(3, KstarRegisters.DSP_VERSION, [100])

        # Block5: 시리얼번호 (FC04, 3228~3238, 11 regs ASCII)
        serial = "KST2024001234"
        serial_bytes = serial.encode('ascii').ljust(22, b'\x00')
        serial_regs = [(serial_bytes[i * 2] << 8) | serial_bytes[i * 2 + 1] for i in range(11)]
        self.store.setValues(4, KstarRegisters.SERIAL_NUMBER_BASE, serial_regs)

    def _get_sun_factor(self):
        """현재 시각 기반 일사량 팩터 (0.0~1.0)"""
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        if 6.0 <= hour <= 18.0:
            return max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0))
        return 0.0

    def _update_registers(self):
        """실시간 FC04 레지스터 업데이트"""
        sun = self._get_sun_factor()
        sun_var = sun * (1.0 + random.uniform(-0.03, 0.03))

        # Kstar: 밤에 인버터 전원 OFF → Modbus FC04 응답 불가 시뮬레이션
        ir_block = self.store.store.get('i')
        if ir_block and hasattr(ir_block, 'night_off'):
            ir_block.night_off = (sun_var <= 0.01 and self.on_off == 0)

        # AC 출력 먼저 결정 → PV 역산 (효율 97.5%)
        EFFICIENCY = 0.975
        power_cap = self.NOMINAL_POWER * (self.power_limit / 1000.0)

        if sun_var > 0.01 and self.on_off == 0:
            possible_ac_w = sun_var * self.NOMINAL_POWER * EFFICIENCY
            ac_power_w = int(min(possible_ac_w, power_cap))
            pv_total_w = ac_power_w / EFFICIENCY
        else:
            ac_power_w = 0
            pv_total_w = 0

        # PV (DC) — 3개 MPPT (전력 역산)
        pv_voltages = [int(v * (0.95 + 0.05 * sun_var)) for v in self.PV_VOLTAGE_NOMINAL]
        pv_powers_w = [int(pv_total_w / 3 * random.uniform(0.98, 1.02))
                       for _ in range(3)] if pv_total_w > 0 else [0, 0, 0]
        pv_currents = [int(pv_powers_w[i] / (pv_voltages[i] * 0.1) * 100)
                       if pv_voltages[i] > 0 and pv_powers_w[i] > 0 else 0
                       for i in range(3)]

        # FC04: PV 전압 3000~3002 (0.1V), PV4~6 = 0 (미사용)
        self.store.setValues(4, KstarRegisters.PV1_VOLTAGE,
                             pv_voltages + [0, 0, 0])  # 3000~3005
        # PV 전류 3012~3014 (0.01A)
        self.store.setValues(4, KstarRegisters.PV1_CURRENT,
                             pv_currents + [0])          # 3012~3015
        # PV 전력 3024~3026 (W)
        self.store.setValues(4, KstarRegisters.PV1_POWER,
                             pv_powers_w + [0])          # 3024~3027

        # PF / 무효전력 제어
        if self.control_mode == 'PF':
            pf = self.power_factor_set / 1000.0
            pf = max(0.85, min(1.0, abs(pf)))
            if ac_power_w > 0 and pf < 1.0:
                reactive_power_w = ac_power_w * math.tan(math.acos(pf))
                if self.power_factor_set < 0:
                    reactive_power_w = -reactive_power_w
            else:
                reactive_power_w = 0
                pf = 1.0
        else:  # RP mode
            rp_pct = self.reactive_power_set
            if rp_pct >= 32768:
                rp_pct = rp_pct - 65536
            reactive_power_w = self.NOMINAL_POWER * (rp_pct / 1000.0)
            if ac_power_w > 0:
                apparent = math.sqrt(ac_power_w**2 + reactive_power_w**2)
                pf = ac_power_w / apparent if apparent > 0 else 1.0
            else:
                pf = 1.0

        self.total_energy_wh += ac_power_w / 3600.0
        energy_01kwh = int(self.total_energy_wh / 100)   # 0.1kWh 단위
        self.store.setValues(4, KstarRegisters.CUMULATIVE_PRODUCTION_L,
                             [energy_01kwh & 0xFFFF, (energy_01kwh >> 16) & 0xFFFF])

        # 금일 발전량 (0.1kWh 단위, 간단히 누적 에너지의 일부)
        today_01kwh = int(ac_power_w * (time.time() - self.start_time) / 3600 / 100)
        self.store.setValues(4, KstarRegisters.DAILY_PRODUCTION, [today_01kwh & 0xFFFF])

        # 상태: 3046 시스템상태, 3047 인버터상태
        if self.on_off == 1:
            self.store.setValues(4, KstarRegisters.SYSTEM_STATUS, [1])    # Standby
            self.store.setValues(4, KstarRegisters.INVERTER_STATUS, [1])  # Standby
        elif sun_var > 0.01:
            self.store.setValues(4, KstarRegisters.SYSTEM_STATUS, [0])    # Normal
            self.store.setValues(4, KstarRegisters.INVERTER_STATUS, [4])  # Running
        else:
            self.store.setValues(4, KstarRegisters.SYSTEM_STATUS, [1])    # Standby
            self.store.setValues(4, KstarRegisters.INVERTER_STATUS, [1])  # Standby

        # 온도: RADIATOR_TEMP = 3055 (0.1℃), CHASSIS_TEMP = 3057 (0.1℃)
        radiator_temp = int((35.0 + sun_var * 20.0 + random.uniform(-2, 2)) * 10)
        self.store.setValues(4, KstarRegisters.RADIATOR_TEMP, [radiator_temp & 0xFFFF])
        self.store.setValues(4, KstarRegisters.CHASSIS_TEMP,
                             [int(radiator_temp * 0.85) & 0xFFFF])

        # AC 출력 — R/S/T상 (3상 균등 분배, 피상전력 기준 전류 계산)
        per_phase_w = ac_power_w // 3
        ac_v = self.NOMINAL_VOLTAGE  # 3800 = 380.0V
        apparent_w = math.sqrt(ac_power_w**2 + reactive_power_w**2) if ac_power_w > 0 else 0
        ac_cur = int((apparent_w / 3) / (ac_v * 0.1) * 100) if ac_v > 0 else 0  # 0.01A

        # 계통 주파수: GRID_FREQUENCY = 3098 (0.01Hz)
        self.store.setValues(4, KstarRegisters.GRID_FREQUENCY, [self.NOMINAL_FREQUENCY])

        # R상 전압/전류/전력
        self.store.setValues(4, KstarRegisters.INV_R_VOLTAGE, [ac_v])
        self.store.setValues(4, KstarRegisters.INV_R_CURRENT, [ac_cur])
        self.store.setValues(4, KstarRegisters.INV_S_FREQUENCY, [self.NOMINAL_FREQUENCY])
        self.store.setValues(4, KstarRegisters.INV_R_POWER,
                             [per_phase_w & 0xFFFF])  # S16 범위 내 (60kW/3=20kW)

        # S상 전압/전류/전력
        self.store.setValues(4, KstarRegisters.INV_S_VOLTAGE, [ac_v])
        self.store.setValues(4, KstarRegisters.INV_S_CURRENT, [ac_cur])
        self.store.setValues(4, KstarRegisters.INV_S_POWER,
                             [per_phase_w & 0xFFFF])

        # T상 전압/전류/전력
        self.store.setValues(4, KstarRegisters.INV_T_VOLTAGE, [ac_v])
        self.store.setValues(4, KstarRegisters.INV_T_CURRENT, [ac_cur])
        self.store.setValues(4, KstarRegisters.INV_T_POWER,
                             [per_phase_w & 0xFFFF])

        # 계통 R상 전압 (GRID_R_VOLTAGE = 3097)
        self.store.setValues(4, KstarRegisters.GRID_R_VOLTAGE, [ac_v])

        # ── DEA-AVM Real-time Monitoring (0x03E8-0x03FD) ──────────────────────
        def _to_s32_regs(v):
            v = int(v)
            if v < 0: v += 0x100000000
            return [v & 0xFFFF, (v >> 16) & 0xFFFF]

        dea_phase_current = ac_cur // 10  # ac_cur is 0.01A, //10 → 0.1A
        dea_voltage = ac_v               # already 0.1V scale
        dea_active = ac_power_w * 10     # 0.1W
        dea_reactive = int(reactive_power_w)  # 1Var
        dea_pf = int(pf * 1000)          # 0.001
        dea_freq = 600                   # 0.1Hz = 60.0Hz
        is_running = (self.on_off == 0 and sun_var > 0.01)
        dea_status = 0x0001 if is_running else 0

        dea = []
        dea += _to_s32_regs(dea_phase_current)  # L1 Current
        dea += _to_s32_regs(dea_phase_current)  # L2 Current
        dea += _to_s32_regs(dea_phase_current)  # L3 Current
        dea += _to_s32_regs(dea_voltage)         # L1 Voltage
        dea += _to_s32_regs(dea_voltage)         # L2 Voltage
        dea += _to_s32_regs(dea_voltage)         # L3 Voltage
        dea += _to_s32_regs(dea_active)           # Active Power
        dea += _to_s32_regs(dea_reactive)         # Reactive Power
        dea += _to_s32_regs(dea_pf)               # Power Factor
        dea += _to_s32_regs(dea_freq)             # Frequency
        dea += [dea_status & 0xFFFF, 0]           # Status Flags
        self.store.setValues(3, 0x03E8, dea)

        night_off = ir_block.night_off if ir_block and hasattr(ir_block, 'night_off') else False
        self._current = {
            'sun_factor': sun_var,
            'pv_power_kw': sum(pv_powers_w) / 1000.0,
            'ac_power_kw': ac_power_w / 1000.0,
            'reactive_kvar': reactive_power_w / 1000.0,
            'power_factor': pf,
            'voltage': ac_v / 10.0,
            'on_off': 'POWER OFF' if night_off else ('OFF' if self.on_off == 1 else ('Running' if sun_var > 0.01 else 'Standby')),
            'ctrl_mode': self.control_mode,
        }

    def _check_control_changes(self):
        """DER-AVM 제어 레지스터 변경 감지 (Verterking과 동일 로직)"""
        new_onoff = self.store.getValues(3, KstarRegisters.INVERTER_ON_OFF, count=1)[0]
        if new_onoff != self.on_off and new_onoff in [0, 1]:
            self.on_off = new_onoff
            self.logger.info(f"[KST] ON/OFF changed: {'OFF' if new_onoff else 'ON'}")

        new_power = self.store.getValues(3, KstarRegisters.DER_ACTIVE_POWER_PCT, count=1)[0]
        if new_power != self.power_limit and 0 <= new_power <= 1100:
            self.power_limit = new_power
            self.logger.info(f"[KST] Active power limit: {new_power/10:.1f}%")

        new_pf = self.store.getValues(3, KstarRegisters.DER_POWER_FACTOR_SET, count=1)[0]
        if new_pf != self.power_factor_set:
            self.power_factor_set = new_pf
            self.control_mode = 'PF'
            self.logger.info(f"[KST] PF set: {new_pf/1000:.3f} (mode=PF)")

        new_rp = self.store.getValues(3, KstarRegisters.DER_REACTIVE_POWER_PCT, count=1)[0]
        if new_rp != self.reactive_power_set:
            self.reactive_power_set = new_rp
            self.control_mode = 'RP'
            self.logger.info(f"[KST] Reactive power set: {new_rp/10:.1f}% (mode=RP)")

        new_mode = self.store.getValues(3, KstarRegisters.DER_ACTION_MODE, count=1)[0]
        if new_mode != self.operation_mode:
            self.operation_mode = new_mode

        # IV Scan trigger: register 4035 (FC06 write → FC03 holding)
        iv_cmd = self.store.getValues(3, KstarRegisters.IV_SCAN_COMMAND, count=1)[0]
        if iv_cmd != 0 and self.iv_scan_status == 0:
            self.iv_scan_status = 1  # Scanning
            self.iv_scan_start_time = time.time()
            # Reset trigger
            self.store.setValues(3, KstarRegisters.IV_SCAN_COMMAND, [0])
            # Regenerate IV data
            self._init_iv_scan_data()
            # Update status register (FC04 input register 3126)
            self.store.setValues(4, KstarRegisters.IV_SCAN_STATUS, [0x0001])  # low=1 (scanning)
            self.logger.info("[KST] IV Scan started")

        # Auto-complete after duration
        if self.iv_scan_status == 1:
            elapsed = time.time() - self.iv_scan_start_time
            progress = min(int(elapsed / self.IV_SCAN_DURATION * 100), 100)
            # Update progress in status register (high byte = progress%)
            self.store.setValues(4, KstarRegisters.IV_SCAN_STATUS, [(progress << 8) | 0x01])
            if elapsed >= self.IV_SCAN_DURATION:
                self.iv_scan_status = 2  # Finished
                self.store.setValues(4, KstarRegisters.IV_SCAN_STATUS, [0x0002])  # low=2 (finished)
                self.logger.info("[KST] IV Scan completed")

        # Auto-reset after 60s
        if self.iv_scan_status == 2 and time.time() - self.iv_scan_start_time > self.IV_SCAN_DURATION + 60:
            self.iv_scan_status = 0

    def _init_iv_scan_data(self):
        """Pre-populate IV curve data in FC04 registers 5000-7399."""
        from common.Kstar_PV_60kw_registers import generate_iv_voltage_data, generate_iv_current_data
        mppt_count = 3
        strings_per_mppt = 4
        data_points = KstarRegisters.IV_POINTS_PER_STRING  # 100

        for mppt in range(mppt_count):
            voc = 750.0 + mppt * 10.0 + random.uniform(-5, 5)
            v_min = 200.0
            isc = 12.0 + random.uniform(-0.5, 0.5)

            voltages = generate_iv_voltage_data(voc, v_min, data_points)

            for s in range(strings_per_mppt):
                string_idx = mppt * strings_per_mppt + s
                base = KstarRegisters.IV_DATA_BASE + string_idx * KstarRegisters.IV_REGS_PER_STRING
                isc_str = isc + random.uniform(-0.3, 0.3)
                currents = generate_iv_current_data(isc_str, voc, v_min, data_points)

                # Write interleaved V/I pairs to FC04 input registers
                iv_regs = []
                for p in range(data_points):
                    iv_regs.append(voltages[p])   # voltage U16
                    iv_regs.append(currents[p])   # current S16 (as U16)
                self.store.setValues(4, base, iv_regs)


# =============================================================================
# Huawei SUN2000-50KTL Inverter Simulator
# =============================================================================

class HuaweiSimulator:
    """Huawei SUN2000-50KTL Inverter Modbus Simulator - Slave ID 5

    FC03 (Holding Register): 모든 실시간 데이터
    50kW, 4 MPPT, 8 strings (MPPT당 2개)
    레지스터 주소 범위: 32000 ~ 32107
    """

    VERSION = "1.0.0"
    MODEL_NAME = "SUN2000-50KTL-M0"
    NOMINAL_POWER  = 50000   # 50kW
    NOMINAL_VOLTAGE = 380    # V (line-to-line)
    NOMINAL_FREQUENCY = 6000 # 0.01Hz → 60.00Hz

    # MPPT별 PV 전압 공칭값 (0.1V 단위) – MPPT1~4
    PV_VOLTAGE_NOMINAL = [4500, 4480, 4520, 4490]

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("HuaweiSim")
        self.running = False

        self.start_time = time.time()
        self.total_energy_kwh = 3000.0  # 초기 누적 발전량 3000kWh

        self.store = self._create_datastore()
        self._current = {}

    def _create_datastore(self):
        """FC03 Holding Register 데이터스토어 생성
        최대 레지스터 주소 32107 → hr block 최소 크기 32109
        여유 확보를 위해 32120으로 설정
        """
        hr_block = ModbusSequentialDataBlock(0, [0] * 32120)

        store = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0] * 10),
            co=ModbusSequentialDataBlock(0, [0] * 10),
            hr=hr_block,
            ir=ModbusSequentialDataBlock(0, [0] * 10),
        )
        self.store = store
        self._init_registers()
        return store

    def _init_registers(self):
        """초기 레지스터 값 설정"""
        # Running Status: Standby
        self.store.setValues(3, HuaweiRegisters.RUNNINGSTATUS, [1])  # STANDBY=1
        # Fault codes: 0
        self.store.setValues(3, HuaweiRegisters.FAULT_CODE, [0, 0, 0, 0])
        # Grid frequency: 60.00Hz → 6000 (0.01Hz)
        self.store.setValues(3, HuaweiRegisters.FREQUENCY, [self.NOMINAL_FREQUENCY])
        # Power factor: 1.000 → 1000 (0.001)
        self.store.setValues(3, HuaweiRegisters.POWER_FACTOR, [1000])
        # Internal temp: 35.0°C → 350 (0.1°C)
        self.store.setValues(3, HuaweiRegisters.INTERNALTEMPERATURE, [350])

    def _get_sun_factor(self):
        """현재 시각 기반 일사량 팩터 (0.0~1.0)"""
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        if 6.0 <= hour <= 18.0:
            return max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0))
        return 0.0

    def _update_registers(self):
        """FC03 Holding Register 업데이트 (Huawei SUN2000 레지스터 맵)"""
        sun = self._get_sun_factor()
        sun_var = sun * (1.0 + random.uniform(-0.03, 0.03))

        # ── PV 스트링 데이터 (32016~32031, 16 regs) ─────────────────────────
        # 4 MPPT × 2 strings = 8 strings  [V0.1V U16, I0.01A S16] × 8
        pv_regs = []
        for mppt_i in range(4):
            v_nom = self.PV_VOLTAGE_NOMINAL[mppt_i]
            for _ in range(2):
                if sun_var > 0:
                    v = int(v_nom * (0.92 + 0.08 * sun_var) + random.uniform(-10, 10))
                    i = int(sun_var * 1050 + random.uniform(-20, 20))
                    i = max(0, i)
                else:
                    v = 0
                    i = 0
                pv_regs.append(v & 0xFFFF)   # U16 voltage (0.1V)
                pv_regs.append(i & 0xFFFF)   # S16 current (0.01A)
        self.store.setValues(3, HuaweiRegisters.PV_VOLTAGE, pv_regs)  # 16 regs

        # ── DC 입력 전력 (32064~32065, S32, 1W) ──────────────────────────────
        pv_power_w = int(self.NOMINAL_POWER * sun_var * random.uniform(0.97, 1.03)) \
                     if sun_var > 0 else 0
        # S32 big-endian: hi-word first, lo-word second
        self.store.setValues(3, HuaweiRegisters.PV_POWER,
                             [(pv_power_w >> 16) & 0xFFFF, pv_power_w & 0xFFFF])

        # ── AC 3상 전압 (32069~32071, U16, 1V) ───────────────────────────────
        ac_v = self.NOMINAL_VOLTAGE
        self.store.setValues(3, HuaweiRegisters.POWERGRIDPHASE_AVOLTAGE, [ac_v, ac_v, ac_v])

        # ── AC 3상 전류 (32072~32077, S32, 0.001A) ───────────────────────────
        ac_power_w = int(pv_power_w * 0.975)
        phase_ma = int(ac_power_w / 3 / ac_v * 1000) if (ac_power_w > 0 and ac_v > 0) else 0
        # S32 big-endian: hi-word first, lo-word second (×3 phases)
        cur_regs = []
        for _ in range(3):
            cur_regs += [(phase_ma >> 16) & 0xFFFF, phase_ma & 0xFFFF]
        self.store.setValues(3, HuaweiRegisters.POWERGRIDPHASE_ACURRENT, cur_regs)

        # ── 유효전력/무효전력/역률/주파수 (32080~32085) ───────────────────────
        # S32 big-endian: hi-word first, lo-word second
        self.store.setValues(3, HuaweiRegisters.PHASE_AACTIVEPOWER,
                             [(ac_power_w >> 16) & 0xFFFF, ac_power_w & 0xFFFF])
        self.store.setValues(3, HuaweiRegisters.REACTIVE_POWER, [0, 0])   # 무효전력 0
        self.store.setValues(3, HuaweiRegisters.POWER_FACTOR, [1000])      # PF = 1.000
        self.store.setValues(3, HuaweiRegisters.FREQUENCY, [self.NOMINAL_FREQUENCY])

        # ── Running Status (32000) ────────────────────────────────────────────
        status = 3 if sun_var > 0.01 else 1  # ON_GRID=3, STANDBY=1
        self.store.setValues(3, HuaweiRegisters.RUNNINGSTATUS, [status])

        # ── 내부 온도 (32087, S16, 0.1°C) ────────────────────────────────────
        temp = int((35.0 + sun_var * 20.0 + random.uniform(-2, 2)) * 10)
        self.store.setValues(3, HuaweiRegisters.INTERNALTEMPERATURE, [temp & 0xFFFF])

        # ── 누적 발전량 (32106~32107, U32, 1kWh) ─────────────────────────────
        self.total_energy_kwh += ac_power_w / 3600000.0   # W→kWh (1초 간격)
        energy_kwh = int(self.total_energy_kwh)
        # U32 big-endian: hi-word first, lo-word second
        self.store.setValues(3, HuaweiRegisters.CUMULATIVE_ENERGY,
                             [(energy_kwh >> 16) & 0xFFFF, energy_kwh & 0xFFFF])

        # ── DEA-AVM Real-time Monitoring (0x03E8-0x03FD) ──────────────────────
        def _to_s32_regs(v):
            v = int(v)
            if v < 0: v += 0x100000000
            return [v & 0xFFFF, (v >> 16) & 0xFFFF]

        dea_phase_current = int(phase_ma / 100)  # phase_ma is 0.001A, /100 → 0.1A
        dea_voltage = 3800                        # 0.1V = 380V
        dea_active = ac_power_w * 10              # 0.1W
        dea_reactive = 0                          # 1Var (no reactive power modeled)
        dea_pf = 1000                             # 0.001 = 1.000
        dea_freq = 600                            # 0.1Hz = 60.0Hz
        is_running = sun_var > 0.01
        dea_status = 0x0001 if is_running else 0

        dea = []
        dea += _to_s32_regs(dea_phase_current)  # L1 Current
        dea += _to_s32_regs(dea_phase_current)  # L2 Current
        dea += _to_s32_regs(dea_phase_current)  # L3 Current
        dea += _to_s32_regs(dea_voltage)         # L1 Voltage
        dea += _to_s32_regs(dea_voltage)         # L2 Voltage
        dea += _to_s32_regs(dea_voltage)         # L3 Voltage
        dea += _to_s32_regs(dea_active)           # Active Power
        dea += _to_s32_regs(dea_reactive)         # Reactive Power
        dea += _to_s32_regs(dea_pf)               # Power Factor
        dea += _to_s32_regs(dea_freq)             # Frequency
        dea += [dea_status & 0xFFFF, 0]           # Status Flags
        self.store.setValues(3, 0x03E8, dea)

        self._current = {
            'sun_factor': sun_var,
            'pv_power_kw': pv_power_w / 1000.0,
            'ac_power_kw': ac_power_w / 1000.0,
            'voltage': ac_v,
            'status': 'On-grid' if sun_var > 0.01 else 'Standby',
        }


# =============================================================================
# Ekos Inverter Simulator
# =============================================================================

class EkosSimulator:
    """Ekos Inverter Modbus Simulator

    FC03 (Holding Register): Float32 data registers + DER-AVM control
    SCALE['power'] = 1.0 → stores raw W (NOT W×10)
    Float32 for power, voltage, current, frequency, power factor
    2 MPPT, 4 strings
    """

    VERSION = "1.0.0"
    MODEL_NAME = "EKOS-50K-3P"
    NOMINAL_POWER = 50000       # 50kW
    NOMINAL_VOLTAGE = 380.0     # V (line-to-line)
    NOMINAL_FREQUENCY = 60.0    # Hz

    # MPPT nominal voltages (V, real float)
    PV_VOLTAGE_NOMINAL = [390.0, 385.0]

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("EkosSim")
        self.running = False

        self.start_time = time.time()
        self.total_energy_wh = 2000000.0   # Initial cumulative: 2000kWh in Wh

        # DER-AVM Control states
        self.on_off = 0                # 0=ON, 1=OFF
        self.power_limit = 1000        # 0.1% units (1000 = 100%)
        self.power_factor_set = 1000   # 0.001 units (1000 = 1.0)
        self.reactive_power_set = 0    # 0.1% units
        self.control_mode = 'PF'
        self.operation_mode = 0

        self.store = self._create_datastore()
        self._current = {}

    @staticmethod
    def _float32_to_regs(value):
        """Convert a Python float to two U16 registers (big-endian Float32)"""
        packed = struct.pack('>f', value)
        hi = (packed[0] << 8) | packed[1]
        lo = (packed[2] << 8) | packed[3]
        return [hi, lo]

    def _create_datastore(self):
        """FC03 Holding Register datastore"""
        # Max register: 0x0834 = 2100, plus DER control area
        hr_block = ModbusLoggedHoldingBlock(
            0, [0] * 0x8500,
            self.logger, simulator=self, name="EKOS"
        )

        store = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0] * 10),
            co=ModbusSequentialDataBlock(0, [0] * 10),
            hr=hr_block,
            ir=ModbusSequentialDataBlock(0, [0] * 10),
        )
        self.store = store
        self._init_control_registers()
        return store

    def _init_control_registers(self):
        """DER-AVM control register initial values"""
        self.store.store['h']._internal_update = True
        try:
            self.store.setValues(3, EkosRegisters.DER_POWER_FACTOR_SET, [self.power_factor_set])
            self.store.setValues(3, EkosRegisters.DER_ACTION_MODE, [self.operation_mode])
            self.store.setValues(3, EkosRegisters.DER_REACTIVE_POWER_PCT, [self.reactive_power_set])
            self.store.setValues(3, EkosRegisters.DER_ACTIVE_POWER_PCT, [self.power_limit])
            self.store.setValues(3, EkosRegisters.INVERTER_ON_OFF, [self.on_off])
        finally:
            self.store.store['h']._internal_update = False

    def _get_sun_factor(self):
        """Time-based sun intensity (0.0~1.0)"""
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        if 6.0 <= hour <= 18.0:
            return max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0))
        return 0.0

    def _update_registers(self):
        """Update FC03 registers — Float32 for analog values, raw W for power"""
        sun = self._get_sun_factor()
        sun_var = sun * (1.0 + random.uniform(-0.03, 0.03))

        EFFICIENCY = 0.975
        power_cap = self.NOMINAL_POWER * (self.power_limit / 1000.0)

        if sun_var > 0.01 and self.on_off == 0:
            possible_ac_w = sun_var * self.NOMINAL_POWER * EFFICIENCY
            ac_power_w = int(min(possible_ac_w, power_cap))
            pv_total_w = ac_power_w / EFFICIENCY
        else:
            ac_power_w = 0
            pv_total_w = 0

        # PV MPPT data (Float32 voltage V, Float32 current A)
        pv_powers_w = []
        for i in range(2):
            if pv_total_w > 0:
                v = self.PV_VOLTAGE_NOMINAL[i] * (0.92 + 0.08 * sun_var) + random.uniform(-2, 2)
                pw = pv_total_w / 2 * random.uniform(0.98, 1.02)
                c = pw / v if v > 0 else 0
                pv_powers_w.append(pw)
            else:
                v = 0.0
                c = 0.0
                pv_powers_w.append(0)

            v_addr = [EkosRegisters.MPPT1_VOLTAGE, EkosRegisters.MPPT2_VOLTAGE][i]
            c_addr = [EkosRegisters.MPPT1_CURRENT, EkosRegisters.MPPT2_CURRENT][i]
            self.store.setValues(3, v_addr, self._float32_to_regs(v))
            self.store.setValues(3, c_addr, self._float32_to_regs(c))

        # PV total power (Float32, raw W)
        self.store.setValues(3, EkosRegisters.PV_POWER,
                             self._float32_to_regs(float(sum(pv_powers_w))))

        # PF / reactive power control
        if self.control_mode == 'PF':
            pf = self.power_factor_set / 1000.0
            pf = max(0.85, min(1.0, abs(pf)))
            if ac_power_w > 0 and pf < 1.0:
                reactive_power_w = ac_power_w * math.tan(math.acos(pf))
                if self.power_factor_set < 0:
                    reactive_power_w = -reactive_power_w
            else:
                reactive_power_w = 0
                pf = 1.0
        else:
            rp_pct = self.reactive_power_set
            if rp_pct >= 32768:
                rp_pct = rp_pct - 65536
            reactive_power_w = self.NOMINAL_POWER * (rp_pct / 1000.0)
            if ac_power_w > 0:
                apparent = math.sqrt(ac_power_w**2 + reactive_power_w**2)
                pf = ac_power_w / apparent if apparent > 0 else 1.0
            else:
                pf = 1.0

        # AC power (Float32, raw W)
        self.store.setValues(3, EkosRegisters.AC_POWER,
                             self._float32_to_regs(float(ac_power_w)))

        # AC 3-phase voltage (Float32, V)
        for v_addr in [EkosRegisters.R_PHASE_VOLTAGE, EkosRegisters.S_PHASE_VOLTAGE,
                       EkosRegisters.T_PHASE_VOLTAGE]:
            self.store.setValues(3, v_addr,
                                 self._float32_to_regs(self.NOMINAL_VOLTAGE))

        # AC 3-phase current (Float32, A)
        apparent_w = math.sqrt(ac_power_w**2 + reactive_power_w**2) if ac_power_w > 0 else 0
        phase_current_a = (apparent_w / 3) / self.NOMINAL_VOLTAGE if self.NOMINAL_VOLTAGE > 0 else 0
        for c_addr in [EkosRegisters.R_PHASE_CURRENT, EkosRegisters.S_PHASE_CURRENT,
                       EkosRegisters.T_PHASE_CURRENT]:
            self.store.setValues(3, c_addr,
                                 self._float32_to_regs(phase_current_a))

        # Frequency (Float32, Hz)
        self.store.setValues(3, EkosRegisters.FREQUENCY,
                             self._float32_to_regs(self.NOMINAL_FREQUENCY))

        # Power factor (Float32)
        self.store.setValues(3, EkosRegisters.POWER_FACTOR,
                             self._float32_to_regs(pf))

        # Inverter mode (U16) — use EKOS raw status values, not Solarize abstract modes
        # 0x0000=Stop, 0x0002=Waiting(Standby), 0x0008=MPP(On-Grid), 0x0009=Fault
        if self.on_off == 1:
            mode = 0x0000  # Stop
        elif sun_var > 0.01:
            mode = 0x0008  # MPP (On-Grid)
        else:
            mode = 0x0002  # Waiting Time (Standby)
        self.store.setValues(3, EkosRegisters.INVERTER_MODE, [mode])

        # Inner temp (U16)
        temp = int(35.0 + sun_var * 20.0 + random.uniform(-2, 2))
        self.store.setValues(3, EkosRegisters.INNER_TEMP, [temp & 0xFFFF])

        # Error codes (U16)
        self.store.setValues(3, EkosRegisters.ERROR_CODE1, [0])
        self.store.setValues(3, EkosRegisters.ERROR_CODE2, [0])

        # Total energy (Float32, Wh)
        self.total_energy_wh += ac_power_w / 3600.0
        self.store.setValues(3, EkosRegisters.TOTAL_ENERGY,
                             self._float32_to_regs(float(self.total_energy_wh)))

        # String currents (U16, 0.01A)
        string_addrs = [EkosRegisters.STRING1_CURRENT, EkosRegisters.STRING2_CURRENT,
                        EkosRegisters.STRING3_CURRENT, EkosRegisters.STRING4_CURRENT]
        for i, s_addr in enumerate(string_addrs):
            mppt_i = i // 2
            if pv_total_w > 0:
                v = self.PV_VOLTAGE_NOMINAL[mppt_i] * (0.92 + 0.08 * sun_var)
                str_c = (pv_total_w / 2 / 2) / v if v > 0 else 0  # per-string current
                self.store.setValues(3, s_addr, [int(str_c * 100) & 0xFFFF])
            else:
                self.store.setValues(3, s_addr, [0])

        # ── DEA-AVM Real-time Monitoring (0x03E8-0x03FD) ──────────────────────
        def _to_s32_regs(v):
            v = int(v)
            if v < 0: v += 0x100000000
            return [v & 0xFFFF, (v >> 16) & 0xFFFF]

        ac_v = self.NOMINAL_VOLTAGE  # 380.0 V (float)
        dea_phase_current = int(phase_current_a * 10)  # A → 0.1A
        dea_voltage = int(ac_v * 10)                    # V → 0.1V
        dea_active = int(ac_power_w * 10)               # W → 0.1W
        dea_reactive = int(reactive_power_w)             # 1Var
        dea_pf = int(pf * 1000)                          # 0.001
        dea_freq = 600                                   # 0.1Hz = 60.0Hz
        is_running = (self.on_off == 0 and sun_var > 0.01)
        dea_status = 0x0001 if is_running else 0

        dea = []
        dea += _to_s32_regs(dea_phase_current)  # L1 Current
        dea += _to_s32_regs(dea_phase_current)  # L2 Current
        dea += _to_s32_regs(dea_phase_current)  # L3 Current
        dea += _to_s32_regs(dea_voltage)         # L1 Voltage
        dea += _to_s32_regs(dea_voltage)         # L2 Voltage
        dea += _to_s32_regs(dea_voltage)         # L3 Voltage
        dea += _to_s32_regs(dea_active)           # Active Power
        dea += _to_s32_regs(dea_reactive)         # Reactive Power
        dea += _to_s32_regs(dea_pf)               # Power Factor
        dea += _to_s32_regs(dea_freq)             # Frequency
        dea += [dea_status & 0xFFFF, 0]           # Status Flags
        self.store.setValues(3, 0x03E8, dea)

        self._current = {
            'sun_factor': sun_var,
            'pv_power_kw': sum(pv_powers_w) / 1000.0,
            'ac_power_kw': ac_power_w / 1000.0,
            'reactive_kvar': reactive_power_w / 1000.0,
            'power_factor': pf,
            'voltage': self.NOMINAL_VOLTAGE,
            'status': 'OFF' if self.on_off == 1 else ('Running' if sun_var > 0.01 else 'Standby'),
            'ctrl_mode': self.control_mode,
        }

    def _check_control_changes(self):
        """DER-AVM control register change detection"""
        new_onoff = self.store.getValues(3, EkosRegisters.INVERTER_ON_OFF, count=1)[0]
        if new_onoff != self.on_off and new_onoff in [0, 1]:
            self.on_off = new_onoff
            self.logger.info(f"[EKOS] ON/OFF changed: {'OFF' if new_onoff else 'ON'}")

        new_power = self.store.getValues(3, EkosRegisters.DER_ACTIVE_POWER_PCT, count=1)[0]
        if new_power != self.power_limit and 0 <= new_power <= 1100:
            self.power_limit = new_power
            self.logger.info(f"[EKOS] Active power limit: {new_power/10:.1f}%")

        new_pf = self.store.getValues(3, EkosRegisters.DER_POWER_FACTOR_SET, count=1)[0]
        if new_pf != self.power_factor_set:
            self.power_factor_set = new_pf
            self.control_mode = 'PF'
            self.logger.info(f"[EKOS] PF set: {new_pf/1000:.3f} (mode=PF)")

        new_rp = self.store.getValues(3, EkosRegisters.DER_REACTIVE_POWER_PCT, count=1)[0]
        if new_rp != self.reactive_power_set:
            self.reactive_power_set = new_rp
            self.control_mode = 'RP'
            self.logger.info(f"[EKOS] Reactive power set: {new_rp/10:.1f}% (mode=RP)")

        new_mode = self.store.getValues(3, EkosRegisters.DER_ACTION_MODE, count=1)[0]
        if new_mode != self.operation_mode:
            self.operation_mode = new_mode


# =============================================================================
# Sungrow Inverter Simulator
# =============================================================================

class SungrowSimulator:
    """Sungrow Inverter Modbus Simulator

    FC03 (Holding Register): U16/U32 data registers + DER-AVM control
    SCALE['power'] = 1.0 → stores raw W (NOT W×10)
    Voltage: 0.1V (U16), Current: 0.1A (U16), Power: raw W (U32)
    4 MPPT, 8 strings
    """

    VERSION = "1.0.0"
    MODEL_NAME = "SG50CX"
    NOMINAL_POWER = 50000       # 50kW
    NOMINAL_VOLTAGE = 3800      # 0.1V → 380V (line-to-line)
    NOMINAL_FREQUENCY = 600     # 0.1Hz → 60.0Hz

    # MPPT nominal voltages (0.1V)
    PV_VOLTAGE_NOMINAL = [3900, 3850, 3920, 3880]

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("SungrowSim")
        self.running = False

        self.start_time = time.time()
        self.total_energy_01kwh = 20000  # Initial: 2000.0 kWh in 0.1kWh units
        self.today_energy_wh = 0.0

        # DER-AVM Control states
        self.on_off = 0                # 0=ON, 1=OFF
        self.power_limit = 1000        # 0.1% units (1000 = 100%)
        self.power_factor_set = 1000   # 0.001 units (1000 = 1.0)
        self.reactive_power_set = 0    # 0.1% units
        self.control_mode = 'PF'
        self.operation_mode = 0

        self.store = self._create_datastore()
        self._current = {}

    def _create_datastore(self):
        """FC03 Holding Register datastore"""
        # Max register: 0x0834 = 2100, plus data registers up to ~0x0200
        hr_block = ModbusLoggedHoldingBlock(
            0, [0] * 0x8500,
            self.logger, simulator=self, name="SGW"
        )

        store = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0] * 10),
            co=ModbusSequentialDataBlock(0, [0] * 10),
            hr=hr_block,
            ir=ModbusSequentialDataBlock(0, [0] * 10),
        )
        self.store = store
        self._init_control_registers()
        return store

    def _init_control_registers(self):
        """DER-AVM control register initial values"""
        self.store.store['h']._internal_update = True
        try:
            self.store.setValues(3, SungrowRegisters.DER_POWER_FACTOR_SET, [self.power_factor_set])
            self.store.setValues(3, SungrowRegisters.DER_ACTION_MODE, [self.operation_mode])
            self.store.setValues(3, SungrowRegisters.DER_REACTIVE_POWER_PCT, [self.reactive_power_set])
            self.store.setValues(3, SungrowRegisters.DER_ACTIVE_POWER_PCT, [self.power_limit])
            self.store.setValues(3, SungrowRegisters.INVERTER_ON_OFF, [self.on_off])
        finally:
            self.store.store['h']._internal_update = False

    def _get_sun_factor(self):
        """Time-based sun intensity (0.0~1.0)"""
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        if 6.0 <= hour <= 18.0:
            return max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0))
        return 0.0

    def _update_registers(self):
        """Update FC03 registers — U16/U32 formats, raw W for power"""
        sun = self._get_sun_factor()
        sun_var = sun * (1.0 + random.uniform(-0.03, 0.03))

        EFFICIENCY = 0.975
        power_cap = self.NOMINAL_POWER * (self.power_limit / 1000.0)

        if sun_var > 0.01 and self.on_off == 0:
            possible_ac_w = sun_var * self.NOMINAL_POWER * EFFICIENCY
            ac_power_w = int(min(possible_ac_w, power_cap))
            pv_total_w = ac_power_w / EFFICIENCY
        else:
            ac_power_w = 0
            pv_total_w = 0

        # PV MPPT data (voltage 0.1V U16, current 0.1A U16)
        pv_voltages = [int(v * (0.92 + 0.08 * sun_var) + random.uniform(-10, 10))
                       if sun_var > 0.01 else 0 for v in self.PV_VOLTAGE_NOMINAL]
        pv_powers_w = [int(pv_total_w / 4 * random.uniform(0.98, 1.02))
                       for _ in range(4)] if pv_total_w > 0 else [0, 0, 0, 0]
        pv_currents = [int(pv_powers_w[i] / (pv_voltages[i] * 0.1) * 10)
                       if pv_voltages[i] > 0 and pv_powers_w[i] > 0 else 0
                       for i in range(4)]

        # MPPT voltage/current registers (interleaved: V1,V2 then V3,V4, C1,C2 then C3,C4)
        self.store.setValues(3, SungrowRegisters.MPPT1_VOLTAGE,
                             [pv_voltages[0], pv_voltages[1]])
        self.store.setValues(3, SungrowRegisters.MPPT1_CURRENT,
                             [pv_currents[0], pv_currents[1]])
        self.store.setValues(3, SungrowRegisters.MPPT3_VOLTAGE,
                             [pv_voltages[2], pv_voltages[3]])
        self.store.setValues(3, SungrowRegisters.MPPT3_CURRENT,
                             [pv_currents[2], pv_currents[3]])

        # PV total power (U32, raw W) — NOT ×10
        pv_total_w_int = int(pv_total_w)
        self.store.setValues(3, SungrowRegisters.PV_POWER,
                             [pv_total_w_int & 0xFFFF, (pv_total_w_int >> 16) & 0xFFFF])

        # PF / reactive power control
        if self.control_mode == 'PF':
            pf = self.power_factor_set / 1000.0
            pf = max(0.85, min(1.0, abs(pf)))
            if ac_power_w > 0 and pf < 1.0:
                reactive_power_w = ac_power_w * math.tan(math.acos(pf))
                if self.power_factor_set < 0:
                    reactive_power_w = -reactive_power_w
            else:
                reactive_power_w = 0
                pf = 1.0
        else:
            rp_pct = self.reactive_power_set
            if rp_pct >= 32768:
                rp_pct = rp_pct - 65536
            reactive_power_w = self.NOMINAL_POWER * (rp_pct / 1000.0)
            if ac_power_w > 0:
                apparent = math.sqrt(ac_power_w**2 + reactive_power_w**2)
                pf = ac_power_w / apparent if apparent > 0 else 1.0
            else:
                pf = 1.0

        # AC power (U32, raw W) — NOT ×10
        self.store.setValues(3, SungrowRegisters.AC_POWER,
                             [ac_power_w & 0xFFFF, (ac_power_w >> 16) & 0xFFFF])

        # AC 3-phase voltage (U16, 0.1V)
        ac_v = self.NOMINAL_VOLTAGE  # 3800 = 380.0V
        self.store.setValues(3, SungrowRegisters.R_PHASE_VOLTAGE, [ac_v])
        self.store.setValues(3, SungrowRegisters.S_PHASE_VOLTAGE, [ac_v])
        self.store.setValues(3, SungrowRegisters.T_PHASE_VOLTAGE, [ac_v])

        # AC 3-phase current (U16, 0.1A)
        apparent_w = math.sqrt(ac_power_w**2 + reactive_power_w**2) if ac_power_w > 0 else 0
        phase_current_01a = int((apparent_w / 3) / (ac_v * 0.1) * 10) if ac_v > 0 else 0
        self.store.setValues(3, SungrowRegisters.R_PHASE_CURRENT, [phase_current_01a])
        self.store.setValues(3, SungrowRegisters.S_PHASE_CURRENT, [phase_current_01a])
        self.store.setValues(3, SungrowRegisters.T_PHASE_CURRENT, [phase_current_01a])

        # Frequency (U16, 0.1Hz)
        self.store.setValues(3, SungrowRegisters.FREQUENCY, [self.NOMINAL_FREQUENCY])

        # Power factor (S16, 0.001)
        pf_reg = int(pf * 1000)
        self.store.setValues(3, SungrowRegisters.POWER_FACTOR, [pf_reg & 0xFFFF])

        # Inverter mode (U16) — use Sungrow raw codes (not Solarize codes)
        # SungrowStatusConverter maps: 0x0002→ON_GRID, 0x0001→STANDBY, 0x0005→SHUTDOWN
        if self.on_off == 1:
            mode_raw = 0x0005   # Sungrow SHUTDOWN
        elif sun_var > 0.01:
            mode_raw = 0x0002   # Sungrow Running (ON_GRID)
        else:
            mode_raw = 0x0001   # Sungrow STANDBY
        self.store.setValues(3, SungrowRegisters.INVERTER_MODE, [mode_raw])

        # Inner temp (S16, 0.1 C)
        temp = int((35.0 + sun_var * 20.0 + random.uniform(-2, 2)) * 10)
        self.store.setValues(3, SungrowRegisters.INNER_TEMP, [temp & 0xFFFF])

        # Error codes (U16)
        self.store.setValues(3, SungrowRegisters.ERROR_CODE1, [0])
        self.store.setValues(3, SungrowRegisters.ERROR_CODE2, [0])

        # Total energy (U32, 0.1kWh)
        self.today_energy_wh += ac_power_w / 3600.0
        self.total_energy_01kwh += ac_power_w / 3600.0 / 100.0  # W-sec to 0.1kWh
        energy_val = int(self.total_energy_01kwh)
        self.store.setValues(3, SungrowRegisters.TOTAL_ENERGY,
                             [energy_val & 0xFFFF, (energy_val >> 16) & 0xFFFF])

        # String currents (0.1A, same addresses as MPPT currents for strings 1-4,
        # separate registers for strings 5-8)
        for i in range(4):
            s_addr = [SungrowRegisters.STRING5_CURRENT, SungrowRegisters.STRING6_CURRENT,
                      SungrowRegisters.STRING7_CURRENT, SungrowRegisters.STRING8_CURRENT][i]
            if pv_total_w > 0:
                str_c = (pv_total_w / 4 / 2) / (pv_voltages[i] * 0.1) * 10 if pv_voltages[i] > 0 else 0
                self.store.setValues(3, s_addr, [int(str_c) & 0xFFFF])
            else:
                self.store.setValues(3, s_addr, [0])

        # ── DEA-AVM Real-time Monitoring (0x03E8-0x03FD) ──────────────────────
        def _to_s32_regs(v):
            v = int(v)
            if v < 0: v += 0x100000000
            return [v & 0xFFFF, (v >> 16) & 0xFFFF]

        # ac_v is 0.1V (3800), phase_current_01a is 0.1A
        dea_phase_current = phase_current_01a          # already 0.1A
        dea_voltage = ac_v                              # already 0.1V (3800)
        dea_active = ac_power_w * 10                    # W → 0.1W
        dea_reactive = int(reactive_power_w)             # 1Var
        dea_pf = int(pf * 1000)                          # 0.001
        dea_freq = 600                                   # 0.1Hz = 60.0Hz
        is_running = (self.on_off == 0 and sun_var > 0.01)
        dea_status = 0x0001 if is_running else 0

        dea = []
        dea += _to_s32_regs(dea_phase_current)  # L1 Current
        dea += _to_s32_regs(dea_phase_current)  # L2 Current
        dea += _to_s32_regs(dea_phase_current)  # L3 Current
        dea += _to_s32_regs(dea_voltage)         # L1 Voltage
        dea += _to_s32_regs(dea_voltage)         # L2 Voltage
        dea += _to_s32_regs(dea_voltage)         # L3 Voltage
        dea += _to_s32_regs(dea_active)           # Active Power
        dea += _to_s32_regs(dea_reactive)         # Reactive Power
        dea += _to_s32_regs(dea_pf)               # Power Factor
        dea += _to_s32_regs(dea_freq)             # Frequency
        dea += [dea_status & 0xFFFF, 0]           # Status Flags
        self.store.setValues(3, 0x03E8, dea)

        self._current = {
            'sun_factor': sun_var,
            'pv_power_kw': sum(pv_powers_w) / 1000.0,
            'ac_power_kw': ac_power_w / 1000.0,
            'reactive_kvar': reactive_power_w / 1000.0,
            'power_factor': pf,
            'voltage': ac_v / 10.0,
            'status': 'OFF' if self.on_off == 1 else ('Running' if sun_var > 0.01 else 'Standby'),
            'ctrl_mode': self.control_mode,
        }

    def _check_control_changes(self):
        """DER-AVM control register change detection"""
        new_onoff = self.store.getValues(3, SungrowRegisters.INVERTER_ON_OFF, count=1)[0]
        if new_onoff != self.on_off and new_onoff in [0, 1]:
            self.on_off = new_onoff
            self.logger.info(f"[SGW] ON/OFF changed: {'OFF' if new_onoff else 'ON'}")

        new_power = self.store.getValues(3, SungrowRegisters.DER_ACTIVE_POWER_PCT, count=1)[0]
        if new_power != self.power_limit and 0 <= new_power <= 1100:
            self.power_limit = new_power
            self.logger.info(f"[SGW] Active power limit: {new_power/10:.1f}%")

        new_pf = self.store.getValues(3, SungrowRegisters.DER_POWER_FACTOR_SET, count=1)[0]
        if new_pf != self.power_factor_set:
            self.power_factor_set = new_pf
            self.control_mode = 'PF'
            self.logger.info(f"[SGW] PF set: {new_pf/1000:.3f} (mode=PF)")

        new_rp = self.store.getValues(3, SungrowRegisters.DER_REACTIVE_POWER_PCT, count=1)[0]
        if new_rp != self.reactive_power_set:
            self.reactive_power_set = new_rp
            self.control_mode = 'RP'
            self.logger.info(f"[SGW] Reactive power set: {new_rp/10:.1f}% (mode=RP)")

        new_mode = self.store.getValues(3, SungrowRegisters.DER_ACTION_MODE, count=1)[0]
        if new_mode != self.operation_mode:
            self.operation_mode = new_mode


# =============================================================================
# Generic Inverter Simulator (dynamic register module loading)
# =============================================================================

class GenericInverterSimulator:
    """Generic Inverter Simulator — dynamically loads any *_registers.py module.

    Supports all Model Maker v2 generated register files. Falls back gracefully
    when optional attributes (INVERTER_MODE, STRING*_VOLTAGE, etc.) are missing.
    """

    VERSION = "1.0.0"
    NOMINAL_POWER = 50000  # 50kW default

    def __init__(self, protocol_name, logger=None):
        self.protocol_name = protocol_name
        self.logger = logger or logging.getLogger(f"Generic-{protocol_name}")
        self.running = False

        # Load register module dynamically
        self._module = self._load_module(protocol_name)
        self.reg_map = getattr(self._module, 'RegisterMap', None)
        self.inv_mode_cls = getattr(self._module, 'InverterMode', None)
        self.scale = getattr(self._module, 'SCALE', {})
        self.mppt_channels = getattr(self._module, 'MPPT_CHANNELS', 4)
        self.string_channels = getattr(self._module, 'STRING_CHANNELS', 0)
        self._get_mppt_regs = getattr(self._module, 'get_mppt_registers', None)
        self._get_string_regs = getattr(self._module, 'get_string_registers', None)

        # Use FC03 by default (no register file uses RTU_FC_CODE currently)
        self.fc_code = getattr(self._module, 'RTU_FC_CODE', 3)

        # Simulation state
        self.start_time = time.time()
        self.total_energy = 1000.0  # kWh
        self.on_off = 0
        self.power_limit = 1000
        self.power_factor_set = 1000
        self.reactive_power_set = 0
        self.control_mode = 'PF'
        self.operation_mode = 0

        # Resolve scale factors
        self._s_voltage = self.scale.get('voltage', 0.1)
        self._s_current = self.scale.get('current', 0.01)
        self._s_power = self.scale.get('power', 0.1)
        self._s_freq = self.scale.get('frequency', 0.01)

        # PV nominal voltages per MPPT (0.1V units → ~390V)
        self._pv_v_nom = [3900 + i * 50 for i in range(self.mppt_channels)]

        self.store = self._create_datastore()
        self._current = {}
        self._current_lock = threading.Lock()

        self.logger.info(f"[GENERIC] Loaded protocol '{protocol_name}' | "
                         f"MPPT={self.mppt_channels} STR={self.string_channels} FC={self.fc_code:02d}")

    @staticmethod
    def _load_module(protocol_name):
        """Load common/{protocol}_registers.py using same logic as RTU modbus_handler."""
        import importlib, glob as _glob

        common_dir = os.path.join(_PROJECT_ROOT, 'common')

        # 1st: exact name
        module_name = f"common.{protocol_name}_registers"
        try:
            return importlib.import_module(module_name)
        except ImportError:
            pass

        # 2nd: case-insensitive prefix glob
        prefix = protocol_name.split('_')[0].lower()
        candidates = sorted(_glob.glob(os.path.join(common_dir, '*_registers.py')))
        for fpath in candidates:
            fname = os.path.basename(fpath)
            if fname.lower().startswith(prefix) and not fname.startswith('REF_'):
                mod_name = f"common.{fname[:-3]}"
                try:
                    return importlib.import_module(mod_name)
                except ImportError:
                    continue

        raise ImportError(f"Register module for protocol '{protocol_name}' not found in {common_dir}")

    def _find_addr(self, *names):
        """Try multiple attribute names on RegisterMap, return first valid int address or None."""
        if self.reg_map is None:
            return None
        for name in names:
            addr = getattr(self.reg_map, name, None)
            if addr is not None and isinstance(addr, int):
                return addr
        return None

    def _create_datastore(self):
        """Create Modbus datastore — FC03 holding register or FC04 input register."""
        block = ModbusLoggedHoldingBlock(
            0, [0] * 0x8500, self.logger, simulator=self,
            name=self.protocol_name[:3].upper()
        )

        if self.fc_code == 4:
            store = ModbusSlaveContext(
                di=ModbusSequentialDataBlock(0, [0] * 10),
                co=ModbusSequentialDataBlock(0, [0] * 10),
                hr=ModbusSequentialDataBlock(0, [0] * 10),
                ir=block,
            )
        else:
            store = ModbusSlaveContext(
                di=ModbusSequentialDataBlock(0, [0] * 10),
                co=ModbusSequentialDataBlock(0, [0] * 10),
                hr=block,
                ir=ModbusSequentialDataBlock(0, [0] * 10),
            )

        self.store = store
        block._internal_update = True
        self._init_control_registers()
        block._internal_update = False
        return store

    def _init_control_registers(self):
        """Set DER-AVM control register initial values."""
        fc = self.fc_code + 1 if self.fc_code == 4 else 3  # FC04→fc_as_hex=4, FC03→3
        addr = self._find_addr('DER_POWER_FACTOR_SET')
        if addr is not None:
            self.store.setValues(fc, addr, [self.power_factor_set])
        addr = self._find_addr('DER_ACTION_MODE')
        if addr is not None:
            self.store.setValues(fc, addr, [self.operation_mode])
        addr = self._find_addr('DER_REACTIVE_POWER_PCT')
        if addr is not None:
            self.store.setValues(fc, addr, [self.reactive_power_set])
        addr = self._find_addr('DER_ACTIVE_POWER_PCT')
        if addr is not None:
            self.store.setValues(fc, addr, [self.power_limit])
        addr = self._find_addr('INVERTER_ON_OFF')
        if addr is not None:
            self.store.setValues(fc, addr, [self.on_off])

    def _get_sun_factor(self):
        """Time-based sun intensity (0.0~1.0), sine wave 06:00-18:00."""
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        if 6.0 <= hour <= 18.0:
            return max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0))
        return 0.0

    def _set_reg(self, addr, values):
        """Write values to the correct function code store."""
        if addr is None:
            return
        fc = self.fc_code + 1 if self.fc_code == 4 else 3
        self.store.setValues(fc, addr, values)

    def _get_reg(self, addr, count=1):
        """Read values from the correct function code store."""
        if addr is None:
            return [0] * count
        fc = self.fc_code + 1 if self.fc_code == 4 else 3
        return self.store.getValues(fc, addr, count=count)

    def _write_u32(self, low_addr, value):
        """Write a U32 value to low and high word registers."""
        if low_addr is None:
            return
        val = int(value) & 0xFFFFFFFF
        self._set_reg(low_addr, [val & 0xFFFF])
        # Check for _HIGH attribute at low_addr+1
        self._set_reg(low_addr + 1, [(val >> 16) & 0xFFFF])

    def _update_registers(self):
        """Update all register values — called every ~1s by EquipmentSimulator."""
        sun = self._get_sun_factor()
        sun_var = sun * (1.0 + random.uniform(-0.03, 0.03))

        EFFICIENCY = 0.975
        power_cap = self.NOMINAL_POWER * (self.power_limit / 1000.0)

        if sun_var > 0.01 and self.on_off == 0:
            possible_ac_w = sun_var * self.NOMINAL_POWER * EFFICIENCY
            ac_power_w = min(possible_ac_w, power_cap)
            pv_total_w = ac_power_w / EFFICIENCY
        else:
            ac_power_w = 0
            pv_total_w = 0

        # --- MPPT data ---
        n_mppt = self.mppt_channels or 4
        for i in range(n_mppt):
            if pv_total_w > 0:
                mppt_v_raw = int(self._pv_v_nom[i] * (0.92 + 0.08 * sun_var)
                                 + random.uniform(-10, 10))
                mppt_p_w = pv_total_w / n_mppt * random.uniform(0.98, 1.02)
                mppt_c_raw = int(mppt_p_w / (mppt_v_raw * self._s_voltage)
                                 / self._s_current) if mppt_v_raw > 0 else 0
                mppt_p_raw = int(mppt_p_w / self._s_power)
            else:
                mppt_v_raw, mppt_c_raw, mppt_p_raw = 0, 0, 0

            if self._get_mppt_regs:
                try:
                    regs = self._get_mppt_regs(i + 1)
                    # (voltage, current, power_low, power_high)
                    self._set_reg(regs[0], [mppt_v_raw])
                    self._set_reg(regs[1], [mppt_c_raw])
                    self._set_reg(regs[2], [mppt_p_raw & 0xFFFF])
                    self._set_reg(regs[3], [(mppt_p_raw >> 16) & 0xFFFF])
                except (ValueError, IndexError):
                    pass
            else:
                # Scan RegisterMap for MPPTn_VOLTAGE/CURRENT/POWER
                n = i + 1
                v_addr = self._find_addr(f'MPPT{n}_VOLTAGE', f'PV{n}_VOLTAGE')
                c_addr = self._find_addr(f'MPPT{n}_CURRENT', f'PV{n}_CURRENT')
                p_addr = self._find_addr(f'MPPT{n}_POWER')
                self._set_reg(v_addr, [mppt_v_raw])
                self._set_reg(c_addr, [mppt_c_raw])
                if p_addr is not None:
                    self._set_reg(p_addr, [mppt_p_raw & 0xFFFF])
                    self._set_reg(p_addr + 1, [(mppt_p_raw >> 16) & 0xFFFF])

        # --- String data ---
        for i in range(self.string_channels):
            mppt_idx = i // max(1, self.string_channels // max(1, n_mppt))
            mppt_idx = min(mppt_idx, n_mppt - 1)
            if pv_total_w > 0:
                str_v_raw = int(self._pv_v_nom[mppt_idx] * (0.92 + 0.08 * sun_var)
                                + random.uniform(-5, 5))
                str_c_raw = int((pv_total_w / n_mppt / max(1, self.string_channels // n_mppt))
                                / (str_v_raw * self._s_voltage) / self._s_current) if str_v_raw > 0 else 0
            else:
                str_v_raw, str_c_raw = 0, 0

            if self._get_string_regs:
                try:
                    v_addr, c_addr = self._get_string_regs(i + 1)
                    self._set_reg(v_addr, [str_v_raw])
                    self._set_reg(c_addr, [str_c_raw])
                except (ValueError, IndexError):
                    pass
            else:
                n = i + 1
                v_addr = self._find_addr(f'STRING{n}_VOLTAGE')
                c_addr = self._find_addr(f'STRING{n}_CURRENT')
                self._set_reg(v_addr, [str_v_raw])
                self._set_reg(c_addr, [str_c_raw])

        # --- AC phase data ---
        ac_voltage_raw = int(380.0 / self._s_voltage) if ac_power_w > 0 else 0
        ac_freq_raw = int(60.0 / self._s_freq)
        ac_power_raw = int(ac_power_w / self._s_power)
        pv_power_raw = int(pv_total_w / self._s_power)

        # Phase voltages (try multiple naming conventions)
        for names in [('L1_VOLTAGE', 'R_PHASE_VOLTAGE', 'A_PHASE_VOLTAGE'),
                      ('L2_VOLTAGE', 'S_PHASE_VOLTAGE', 'B_PHASE_VOLTAGE'),
                      ('L3_VOLTAGE', 'T_PHASE_VOLTAGE', 'C_PHASE_VOLTAGE')]:
            addr = self._find_addr(*names)
            self._set_reg(addr, [ac_voltage_raw])

        # Phase currents
        apparent_w = math.sqrt(ac_power_w**2) if ac_power_w > 0 else 0
        phase_current_raw = int(apparent_w / 3 / 380.0 / self._s_current) if apparent_w > 0 else 0
        for names in [('L1_CURRENT', 'R_PHASE_CURRENT'),
                      ('L2_CURRENT', 'S_PHASE_CURRENT'),
                      ('L3_CURRENT', 'T_PHASE_CURRENT')]:
            addr = self._find_addr(*names)
            self._set_reg(addr, [phase_current_raw])

        # Frequency
        addr = self._find_addr('FREQUENCY')
        self._set_reg(addr, [ac_freq_raw])

        # AC Power (U32)
        addr = self._find_addr('AC_POWER', 'ACTIVE_POWER')
        if addr is not None:
            self._write_u32(addr, ac_power_raw)

        # PV Power (U32)
        addr = self._find_addr('PV_POWER')
        if addr is not None:
            self._write_u32(addr, pv_power_raw)

        # Power factor
        pf = self.power_factor_set / 1000.0
        pf = max(0.85, min(1.0, abs(pf)))
        pf_raw = int(pf * 1000)
        addr = self._find_addr('POWER_FACTOR')
        self._set_reg(addr, [pf_raw & 0xFFFF])

        # Cumulative energy
        total_kwh = int(self.total_energy)
        addr = self._find_addr('CUMULATIVE_ENERGY', 'CUMULATIVE_ENERGY_LOW', 'TOTAL_ENERGY')
        if addr is not None:
            self._write_u32(addr, total_kwh)

        # Accumulate energy
        if ac_power_w > 0:
            self.total_energy += ac_power_w / 3600000.0  # W·s → kWh

        # Inverter mode
        addr = self._find_addr('INVERTER_MODE')
        if addr is not None:
            if self.inv_mode_cls:
                mode_val = getattr(self.inv_mode_cls, 'STANDBY', 1) if self.on_off == 1 \
                    else getattr(self.inv_mode_cls, 'ON_GRID', 3)
            else:
                mode_val = 1 if self.on_off == 1 else 3
            self._set_reg(addr, [mode_val])

        # Error codes
        for name in ('ERROR_CODE1', 'ERROR_CODE2', 'ERROR_CODE3'):
            addr = self._find_addr(name)
            self._set_reg(addr, [0])

        # Temperature
        addr = self._find_addr('INNER_TEMP', 'INTERNALTEMPERATURE', 'TEMPERATURE',
                               'INVERTER_INNERTEMPERATURE', 'INVERTER_MODULETEMPERATURE')
        self._set_reg(addr, [45])

        # Update display dict (thread-safe)
        with self._current_lock:
            self._current = {
                'sun_factor': sun,
                'pv_power_kw': pv_total_w / 1000.0,
                'ac_power_kw': ac_power_w / 1000.0,
                'voltage': 380.0 if ac_power_w > 0 else 0,
                'on_off': 'ON' if self.on_off == 0 else 'OFF',
                'status': 'Running' if (self.on_off == 0 and sun_var > 0.01) else (
                    'OFF' if self.on_off == 1 else 'Standby'),
                'ctrl_mode': self.control_mode,
            }

    def _check_control_changes(self):
        """Poll DER-AVM control registers for external writes."""
        addr = self._find_addr('INVERTER_ON_OFF')
        if addr is not None:
            val = self._get_reg(addr)[0]
            if val != self.on_off and val in (0, 1):
                self.on_off = val
                self.logger.info(f"[{self.protocol_name}] ON/OFF -> {'OFF' if val else 'ON'}")

        addr = self._find_addr('DER_ACTIVE_POWER_PCT')
        if addr is not None:
            val = self._get_reg(addr)[0]
            if val != self.power_limit and 0 <= val <= 1100:
                self.power_limit = val
                self.logger.info(f"[{self.protocol_name}] Power limit -> {val/10:.1f}%")

        addr = self._find_addr('DER_POWER_FACTOR_SET')
        if addr is not None:
            val = self._get_reg(addr)[0]
            if val != self.power_factor_set:
                self.power_factor_set = val
                self.control_mode = 'PF'
                self.logger.info(f"[{self.protocol_name}] PF -> {val/1000:.3f}")

        addr = self._find_addr('DER_REACTIVE_POWER_PCT')
        if addr is not None:
            val = self._get_reg(addr)[0]
            if val != self.reactive_power_set:
                self.reactive_power_set = val
                self.control_mode = 'RP'
                self.logger.info(f"[{self.protocol_name}] Reactive -> {val/10:.1f}%")

        addr = self._find_addr('DER_ACTION_MODE')
        if addr is not None:
            val = self._get_reg(addr)[0]
            if val != self.operation_mode:
                self.operation_mode = val


# =============================================================================
# =============================================================================
# Broadcast Proxy (slave_id=0)
# =============================================================================

class _BroadcastProxy:
    """Proxy that forwards write operations to all inverter stores.
    Modbus broadcast (slave 0) sends write commands to all devices,
    no response is expected. Read operations return empty data."""

    def __init__(self, stores: list, logger=None):
        self.stores = stores  # list of ModbusLoggedBlock (inverter stores)
        self.logger = logger
        # Minimal SlaveContext-compatible interface
        self.store = self

    def validate(self, *args, **kwargs):
        return True

    def getValues(self, fc_as_hex, address, count=1):
        """Read from first store (broadcast reads are unusual but handle gracefully)."""
        if self.stores:
            return self.stores[0].getValues(address, count)
        return [0] * count

    def setValues(self, fc_as_hex, address, values):
        """Forward write to ALL inverter stores."""
        if self.logger:
            self.logger.info(f"[BROADCAST] Write 0x{address-1:04X} <- {values} to {len(self.stores)} inverters")
        for store in self.stores:
            store.setValues(address, values)


# =============================================================================
# Multi-Device Server
# =============================================================================

class EquipmentSimulator:
    """Multi-Device Equipment Simulator — Dynamic device configuration"""

    VERSION = "1.5.0"

    def __init__(self, config: dict):
        self.port = config.get('port', 'COM10')
        self.baudrate = config.get('baudrate', 9600)
        self.device_config = config.get('devices', [])
        self.running = False
        self.logger = logging.getLogger("EquipSim")

        # Dynamic device creation
        self.devices = []  # list of (slave_id, type, name, protocol, simulator)
        device_map = {}    # slave_id -> store

        # First pass: create inverters and weather
        relay_configs = []
        for dc in self.device_config:
            sid = dc['slave_id']
            dtype = dc['type']
            name = dc['name']
            proto = dc.get('protocol', '')

            if dtype == 'inverter':
                sim = _create_inverter_by_protocol(proto, self.logger)
            elif dtype == 'relay':
                relay_configs.append(dc)
                continue  # Create relay after all inverters
            elif dtype == 'weather':
                sim = WeatherSimulator(self.logger)
            else:
                continue

            self.devices.append({
                'slave_id': sid,
                'type': dtype,
                'name': name,
                'protocol': proto,
                'sim': sim,
            })
            device_map[sid] = sim.store

        # Second pass: create relays with inverter references
        inverter_sims = [d['sim'] for d in self.devices if d['type'] == 'inverter']
        for dc in relay_configs:
            sid = dc['slave_id']
            name = dc['name']
            proto = dc.get('protocol', '')
            sim = RelaySimulator(self.logger, inverter_sims=inverter_sims)
            self.devices.append({
                'slave_id': sid,
                'type': 'relay',
                'name': name,
                'protocol': proto,
                'sim': sim,
            })
            device_map[sid] = sim.store

        # Broadcast support: slave_id=0 writes are forwarded to all inverters
        inverter_stores = [d['sim'].store for d in self.devices if d['type'] == 'inverter']
        if inverter_stores:
            device_map[0] = _BroadcastProxy(inverter_stores, self.logger)

        self.context = ModbusServerContext(devices=device_map, single=False)
    
    def _update_loop(self):
        """Background thread for updating all devices"""
        error_count = 0
        last_error_time = 0.0
        ERROR_RESET_INTERVAL = 60  # 60초 경과 시 에러 카운터 리셋
        while self.running:
            try:
                for d in self.devices:
                    sim = d['sim']
                    sim._update_registers()
                    if hasattr(sim, '_check_control_changes'):
                        sim._check_control_changes()  # _update_registers 이후 실행
                error_count = 0
            except Exception as e:
                now = time.time()
                if error_count > 0 and (now - last_error_time) > ERROR_RESET_INTERVAL:
                    self.logger.info(f"Error counter reset after {ERROR_RESET_INTERVAL}s of inactivity")
                    error_count = 0
                error_count += 1
                last_error_time = now
                self.logger.exception(f"Update error ({error_count}): {e}")
                if error_count >= 10:
                    self.logger.critical("Too many consecutive errors, halting simulator")
                    self.running = False
                    break
            time.sleep(1)
    
    def _display_status(self):
        """Display combined status for all configured devices"""
        while self.running:
            try:
                timestamp = datetime.now().strftime("%H:%M:%S")

                # Wait for all devices to have data
                all_ready = all(d['sim']._current for d in self.devices)
                if not all_ready:
                    time.sleep(1)
                    continue

                if not self.running:
                    break

                print("\033[2J\033[H", end="")
                print("=" * 80)
                print(f"  Equipment Simulator v{self.VERSION} - {timestamp}")
                print(f"  Port: {self.port} | Baud: {self.baudrate} | Devices: {len(self.devices)}")
                print("=" * 80)

                for d in sorted(self.devices, key=lambda x: x['slave_id']):
                    sim = d['sim']
                    try:
                        lock = getattr(sim, '_current_lock', None)
                        if lock:
                            with lock:
                                cur = dict(sim._current)  # Thread-safe snapshot
                        else:
                            cur = dict(sim._current)  # Snapshot to avoid race
                    except RuntimeError:
                        continue  # dict changed size during iteration
                    sid = d['slave_id']
                    dtype = d['type']
                    name = d['name']

                    if dtype == 'inverter':
                        fc = "FC04" if d['protocol'].lower().startswith('kstar') else "FC03"
                        print(f"\n  [{name.upper()}] Slave ID: {sid} | {fc}")
                        print("-" * 80)
                        status = cur.get('on_off', cur.get('status', 'N/A'))
                        sun = cur.get('sun_factor', 0) * 100
                        pv_kw = cur.get('pv_power_kw', 0)
                        ac_kw = cur.get('ac_power_kw', 0)
                        voltage = cur.get('voltage', 0)
                        print(f"  Status: {status} | Sun: {sun:.0f}%")
                        print(f"  PV: {pv_kw:.2f} kW | AC: {ac_kw:.2f} kW | V: {voltage:.1f} V")

                    elif dtype == 'relay':
                        print(f"\n  [RELAY {name}] Slave ID: {sid} | FC03 (PCC)")
                        print("-" * 80)
                        inv_kw = cur.get('inverter_kw', 0)
                        load_kw = cur.get('load_kw', 0)
                        net_kw = cur.get('net_kw', 0)
                        do_st = cur.get('do_status', 0)
                        rx_kwh = cur.get('received_kwh', 0)
                        tx_kwh = cur.get('sent_kwh', 0)
                        direction = "<<EXPORT" if net_kw < 0 else "IMPORT>>"
                        print(f"  INV: {inv_kw:.1f}kW | Load: {load_kw:.1f}kW | Net: {net_kw:.1f}kW [{direction}]")
                        print(f"  DO: 0x{do_st:04X} | +WH: {rx_kwh:.1f}kWh | -WH: {tx_kwh:.1f}kWh")

                    elif dtype == 'weather':
                        print(f"\n  [WEATHER {name}] Slave ID: {sid} | FC03")
                        print("-" * 80)
                        rad = cur.get('radiation', 0)
                        temp = cur.get('air_temp', 0)
                        hum = cur.get('humidity', 0)
                        print(f"  Radiation: {rad:.0f} W/m2 | Temp: {temp:.1f}C | Humidity: {hum:.1f}%")

                    # Show Modbus log if available
                    if hasattr(sim, 'store') and hasattr(sim.store, 'store'):
                        for btype in ('h', 'i'):
                            block = sim.store.store.get(btype)
                            if block and hasattr(block, 'log_queue') and block.log_queue:
                                recent = list(block.log_queue)[-2:]
                                for log in recent:
                                    print(f"    {log}")

                print("\n" + "=" * 80)
                print("  Press Ctrl+C to stop")

                time.sleep(2)

            except Exception as e:
                self.logger.error(f"Display error: {e}")
                time.sleep(2)
    
    def start(self):
        """Start the multi-device simulator"""
        print("\n  Starting Modbus server...")
        print()

        self.running = True
        for d in self.devices:
            d['sim'].running = True

        self._update_thread = threading.Thread(target=self._update_loop, daemon=True, name="update")
        self._update_thread.start()

        self._display_thread = threading.Thread(target=self._display_status, daemon=True, name="display")
        self._display_thread.start()

        try:
            identity = ModbusDeviceIdentification()
            identity.VendorName = 'Solarize'
            identity.ProductCode = 'EQUIP-SIM'
            identity.VendorUrl = 'http://solarize.co.kr'
            identity.ProductName = 'Equipment Simulator'
            identity.ModelName = 'Multi-Device'
            identity.MajorMinorRevision = self.VERSION

            StartSerialServer(
                context=self.context,
                identity=identity,
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=1
            )
        except KeyboardInterrupt:
            print("\n\nShutting down...")
        except Exception as e:
            import traceback
            print(f"\nError: {e}")
            traceback.print_exc()
        finally:
            self.running = False
            for d in self.devices:
                d['sim'].running = False
            # Wait for threads to finish
            if hasattr(self, '_update_thread') and self._update_thread.is_alive():
                self._update_thread.join(timeout=5)
            if hasattr(self, '_display_thread') and self._display_thread.is_alive():
                self._display_thread.join(timeout=5)
            if (hasattr(self, '_update_thread') and self._update_thread.is_alive()) or \
               (hasattr(self, '_display_thread') and self._display_thread.is_alive()):
                self.logger.warning("Threads did not exit gracefully")


def _generate_rtu_config(devices, inv_models):
    """Generate matching RTU rs485_ch1.ini from simulator device config"""
    import configparser

    # Load inverter features from device_models.ini
    ini_path = os.path.join(_PROJECT_ROOT,
                            'config', 'device_models.ini')
    features = {}
    if os.path.isfile(ini_path):
        cfg = configparser.ConfigParser()
        cfg.read(ini_path, encoding='utf-8')
        if cfg.has_section('inverter_features'):
            for mid, vals in cfg.items('inverter_features'):
                parts = [v.strip() for v in vals.split(',')]
                features[int(mid)] = {
                    'iv_scan': parts[0].lower() == 'true' if len(parts) > 0 else False,
                    'kdn': parts[1].lower() == 'true' if len(parts) > 1 else False,
                }

    # Build model lookup: protocol -> model info
    model_by_proto = {}
    for m in inv_models:
        model_by_proto[m['protocol']] = m

    lines = []
    lines.append("# ============================================================================")
    lines.append("# RS485 Channel 1 Device Configuration")
    lines.append("# Auto-generated by Equipment Simulator")
    lines.append(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("# ============================================================================")
    lines.append("")

    for i, dev in enumerate(devices):
        sid = dev['slave_id']
        dtype = dev['type']
        name = dev['name']
        proto = dev.get('protocol', '')
        section = f"device_{i+1}"

        lines.append(f"# ----------------------------------------------------------------------------")

        if dtype == 'inverter':
            model_info = model_by_proto.get(proto, {})
            model_id = model_info.get('id', 1)
            feat = features.get(model_id, {'iv_scan': False, 'kdn': False})

            # Determine mppt/string defaults by protocol
            if proto.lower().startswith('kstar'):
                mppt, string = 3, 9
            elif proto.lower().startswith('huawei'):
                mppt, string = 4, 8
            else:
                mppt, string = 4, 8

            iv_scan_str = 'true' if feat['iv_scan'] else 'false'
            control = 'DER_AVM' if feat['kdn'] else 'NONE'

            lines.append(f"# Inverter {i+1} - {name} (protocol: {proto})")
            lines.append(f"# ----------------------------------------------------------------------------")
            lines.append(f"[{section}]")
            lines.append(f"slave_id = {sid}")
            lines.append(f"installed = YES")
            lines.append(f"device_number = {i+1}")
            lines.append(f"device_type = 1")
            lines.append(f"protocol = {proto}")
            lines.append(f"model = {model_id}")
            lines.append(f"mppt_count = {mppt}")
            lines.append(f"string_count = {string}")
            lines.append(f"iv_scan = {iv_scan_str}")
            lines.append(f"iv_scan_data_points = 64")
            lines.append(f"control = {control}")
            lines.append(f"zee_control = false")
            lines.append(f"simulation = false")

        elif dtype == 'relay':
            proto = dev.get('protocol', 'relay')
            mid = dev.get('model_id', 1)
            lines.append(f"# Protection Relay {i+1} - {name} (protocol: {proto})")
            lines.append(f"# ----------------------------------------------------------------------------")
            lines.append(f"[{section}]")
            lines.append(f"slave_id = {sid}")
            lines.append(f"installed = YES")
            lines.append(f"device_number = {i+1}")
            lines.append(f"device_type = 4")
            lines.append(f"protocol = {proto}")
            lines.append(f"model = {mid}")
            lines.append(f"iv_scan = false")
            lines.append(f"control = NONE")
            lines.append(f"zee_control = false")
            lines.append(f"simulation = false")

        elif dtype == 'weather':
            proto = dev.get('protocol', 'weather')
            mid = dev.get('model_id', 1)
            lines.append(f"# Weather Station {i+1} - {name} (protocol: {proto})")
            lines.append(f"# ----------------------------------------------------------------------------")
            lines.append(f"[{section}]")
            lines.append(f"slave_id = {sid}")
            lines.append(f"installed = YES")
            lines.append(f"device_number = {i+1}")
            lines.append(f"device_type = 5")
            lines.append(f"protocol = {proto}")
            lines.append(f"model = {mid}")
            lines.append(f"simulation = false")

        lines.append("")

    # Save to config directory as rs485_ch1.ini (dashboard Config -> Apply & Restart)
    config_dir = os.path.join(_PROJECT_ROOT, 'config')
    out_path = os.path.join(config_dir, 'rs485_ch1.ini')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"  RTU config saved: {out_path}")
    print("  -> Dashboard Config tab -> Apply & Restart RTU")


def _load_device_models_ini():
    """Load all device models from device_models.ini"""
    import configparser
    ini_path = os.path.join(_PROJECT_ROOT,
                            'config', 'device_models.ini')
    result = {'inverter': [], 'relay': [], 'weather': []}
    if os.path.isfile(ini_path):
        cfg = configparser.ConfigParser()
        cfg.read(ini_path, encoding='utf-8')
        # Inverters
        names = dict(cfg.items('inverter_models')) if cfg.has_section('inverter_models') else {}
        protocols = dict(cfg.items('inverter_protocols')) if cfg.has_section('inverter_protocols') else {}
        for mid, name in sorted(names.items(), key=lambda x: int(x[0])):
            proto = protocols.get(mid, 'unknown')
            result['inverter'].append({'id': int(mid), 'name': name, 'protocol': proto})
        # Relays
        names = dict(cfg.items('relay_models')) if cfg.has_section('relay_models') else {}
        protocols = dict(cfg.items('relay_protocols')) if cfg.has_section('relay_protocols') else {}
        for mid, name in sorted(names.items(), key=lambda x: int(x[0])):
            proto = protocols.get(mid, 'relay')
            result['relay'].append({'id': int(mid), 'name': name, 'protocol': proto})
        # Weather
        names = dict(cfg.items('weather_models')) if cfg.has_section('weather_models') else {}
        protocols = dict(cfg.items('weather_protocols')) if cfg.has_section('weather_protocols') else {}
        for mid, name in sorted(names.items(), key=lambda x: int(x[0])):
            proto = protocols.get(mid, 'weather')
            result['weather'].append({'id': int(mid), 'name': name, 'protocol': proto})
    # Defaults
    if not result['inverter']:
        result['inverter'] = [
            {'id': 1, 'name': 'Solarize Verterking', 'protocol': 'solarize'},
            {'id': 2, 'name': 'Huawei SUN2000', 'protocol': 'huawei'},
            {'id': 3, 'name': 'Kstar KSG-60KT', 'protocol': 'kstar'},
        ]
    if not result['relay']:
        result['relay'] = [{'id': 1, 'name': 'KDU-300', 'protocol': 'relay'}]
    if not result['weather']:
        result['weather'] = [{'id': 1, 'name': 'SEM5046', 'protocol': 'weather'}]
    return result


def _load_inverter_models():
    """Load inverter models (backward compatible)"""
    return _load_device_models_ini()['inverter']


def _create_inverter_by_protocol(protocol, logger):
    """Create inverter simulator by protocol name"""
    p = protocol.lower()
    if p.startswith('solarize') or p == 'verterking':
        return InverterSimulator(logger)  # W×10, SCALE power=0.1
    elif p.startswith('kstar'):
        return KstarSimulator(logger)
    elif p.startswith('huawei'):
        return HuaweiSimulator(logger)
    elif p.startswith('ekos'):
        return EkosSimulator(logger)
    elif p.startswith('sungrow'):
        return SungrowSimulator(logger)
    else:
        # Generic: dynamically load register module for any protocol
        return GenericInverterSimulator(protocol, logger)


def _load_config_from_ini():
    """Read rs485_ch1.ini + rtu_config.ini -> device list for simulator.

    New default config source (replaces simulator_config.json).
    rs485_ch1.ini is the single source of truth for device configuration,
    shared by both RTU and simulator.
    """
    import configparser
    config_dir = os.path.join(_PROJECT_ROOT, 'config')
    ini_path = os.path.join(config_dir, 'rs485_ch1.ini')

    if not os.path.isfile(ini_path):
        raise FileNotFoundError(f"rs485_ch1.ini not found: {ini_path}")

    cfg = configparser.ConfigParser()
    cfg.read(ini_path, encoding='utf-8')

    # Load model names from device_models.ini for display
    all_models = _load_device_models_ini()
    model_names = {}
    for dtype_key in ('inverter', 'relay', 'weather'):
        for m in all_models[dtype_key]:
            model_names[(dtype_key, m['protocol'])] = m['name']

    # device_type mapping: 1=inverter, 4=relay, 5=weather
    dtype_map = {1: 'inverter', 4: 'relay', 5: 'weather'}

    devices = []
    for section in sorted(cfg.sections()):
        if not section.startswith('device_'):
            continue
        installed = cfg.get(section, 'installed', fallback='NO').strip().upper()
        if installed != 'YES':
            continue

        sid = cfg.getint(section, 'slave_id')
        dtype_id = cfg.getint(section, 'device_type', fallback=1)
        protocol = cfg.get(section, 'protocol', fallback='solarize').strip()
        model_id = cfg.getint(section, 'model', fallback=1)
        name = cfg.get(section, 'name', fallback='').strip()

        dtype = dtype_map.get(dtype_id, 'inverter')

        # Auto-generate name from device_models.ini or protocol
        if not name:
            name = model_names.get((dtype, protocol), f'{protocol.title()} #{sid}')

        devices.append({
            'slave_id': sid,
            'type': dtype,
            'name': name,
            'protocol': protocol,
            'model_id': model_id,
        })

    # COM 포트: PC에서 USB-RS485 자동 감지 (rtu_config.ini는 CM4용이라 무시)
    port = 'COM10'
    baudrate = 9600
    try:
        import serial.tools.list_ports
        usb_ports = [p.device for p in serial.tools.list_ports.comports()
                     if 'USB' in (p.description or '').upper()]
        if usb_ports:
            port = usb_ports[0]
    except Exception:
        pass

    return {'port': port, 'baudrate': baudrate, 'devices': devices}


def _interactive_setup():
    """Interactive device configuration (fallback when rs485_ch1.ini missing)"""
    print("=" * 70)
    print("  Equipment Simulator v1.4.0")
    print("  Modbus RTU Slave Simulator (Multi-Device)")
    print("=" * 70)
    print()

    # COM port
    port_input = input("  COM Port [COM10]: ").strip()
    port = port_input if port_input else "COM10"

    baud_input = input("  Baudrate [9600]: ").strip()
    baudrate = int(baud_input) if baud_input else 9600

    # Device count
    print()
    count_input = input("  Number of devices [5]: ").strip()
    device_count = int(count_input) if count_input else 5

    all_models = _load_device_models_ini()
    inv_models = all_models['inverter']
    relay_models = all_models['relay']
    weather_models = all_models['weather']

    devices = []
    for i in range(device_count):
        print(f"\n  --- Device {i+1} (Slave ID {i+1}) ---")
        sid_input = input(f"    Slave ID [{i+1}]: ").strip()
        slave_id = int(sid_input) if sid_input else (i + 1)

        print("    Device type:")
        print("      [1] Inverter")
        print("      [2] Protection Relay")
        print("      [3] Weather Station")
        type_input = input("    Select [1]: ").strip()
        dev_type = int(type_input) if type_input else 1

        if dev_type == 1:
            print("    Inverter model:")
            for j, m in enumerate(inv_models):
                print(f"      [{j+1}] {m['name']} (protocol: {m['protocol']})")
            proto_input = input(f"    Select [1]: ").strip()
            proto_idx = int(proto_input) - 1 if proto_input else 0
            proto_idx = max(0, min(proto_idx, len(inv_models) - 1))
            selected = inv_models[proto_idx]
            devices.append({
                'slave_id': slave_id,
                'type': 'inverter',
                'name': selected['name'],
                'protocol': selected['protocol'],
                'model_id': selected['id'],
            })
        elif dev_type == 2:
            print("    Relay model:")
            for j, m in enumerate(relay_models):
                print(f"      [{j+1}] {m['name']} (protocol: {m['protocol']})")
            r_input = input(f"    Select [1]: ").strip()
            r_idx = int(r_input) - 1 if r_input else 0
            r_idx = max(0, min(r_idx, len(relay_models) - 1))
            selected = relay_models[r_idx]
            devices.append({
                'slave_id': slave_id,
                'type': 'relay',
                'name': selected['name'],
                'protocol': selected['protocol'],
                'model_id': selected['id'],
            })
        elif dev_type == 3:
            print("    Weather station model:")
            for j, m in enumerate(weather_models):
                print(f"      [{j+1}] {m['name']} (protocol: {m['protocol']})")
            w_input = input(f"    Select [1]: ").strip()
            w_idx = int(w_input) - 1 if w_input else 0
            w_idx = max(0, min(w_idx, len(weather_models) - 1))
            selected = weather_models[w_idx]
            devices.append({
                'slave_id': slave_id,
                'type': 'weather',
                'name': selected['name'],
                'protocol': selected['protocol'],
                'model_id': selected['id'],
            })

    # Summary
    print("\n" + "=" * 70)
    print("  Device Configuration:")
    for d in devices:
        fc = "FC04" if d['protocol'].lower().startswith('kstar') else "FC03"
        if d['type'] != 'inverter':
            fc = "FC03"
        print(f"    [Slave {d['slave_id']}] {d['type'].title():10s} - {d['name']} ({fc})")
    print("=" * 70)

    # Save config
    config = {
        'port': port,
        'baudrate': baudrate,
        'devices': devices,
    }
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'simulator_config.json')
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print(f"  Config saved: {config_path}")

    return config


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    )

    parser = argparse.ArgumentParser(
        description='Equipment Simulator v1.4.0'
    )
    parser.add_argument('--port', type=str, default=None,
                        help='Serial port (overrides config)')
    parser.add_argument('--baudrate', type=int, default=None,
                        help='Baudrate (overrides config)')
    parser.add_argument('--config', type=str, default=None,
                        help='Load config JSON (backward compat, overrides INI)')
    # Legacy args for backward compatibility
    parser.add_argument('--inverter-id', type=int, default=None)
    parser.add_argument('--relay-id', type=int, default=None)
    parser.add_argument('--kstar-id', type=int, default=None)
    parser.add_argument('--huawei-id', type=int, default=None)

    args = parser.parse_args()

    config = None

    # Legacy mode: if old-style args provided
    if args.inverter_id is not None or args.relay_id is not None:
        inv_id = args.inverter_id or 1
        relay_id = args.relay_id or 2
        kstar_id = args.kstar_id or 4
        huawei_id = args.huawei_id or 5
        config = {
            'port': args.port or 'COM10',
            'baudrate': args.baudrate or 9600,
            'devices': [
                {'slave_id': inv_id, 'type': 'inverter', 'name': 'Solarize Verterking', 'protocol': 'solarize'},
                {'slave_id': relay_id, 'type': 'relay', 'name': 'KDU-300', 'protocol': 'relay', 'model_id': 1},
                {'slave_id': 3, 'type': 'weather', 'name': 'SEM5046', 'protocol': 'weather', 'model_id': 1},
                {'slave_id': kstar_id, 'type': 'inverter', 'name': 'Kstar KSG-60KT', 'protocol': 'kstar'},
                {'slave_id': huawei_id, 'type': 'inverter', 'name': 'Huawei SUN2000', 'protocol': 'huawei'},
            ]
        }
    elif args.config:
        # Priority 1: CLI --config (JSON file, backward compat)
        try:
            with open(args.config, encoding='utf-8') as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"  [ERROR] Config file error: {e}")
            sys.exit(1)
    else:
        # Priority 2: Read from rs485_ch1.ini (new default)
        try:
            config = _load_config_from_ini()
            if config.get('devices'):
                # 시뮬레이션 가능한 모델 리스트 (common/ 스캔)
                common_dir = os.path.join(_PROJECT_ROOT, 'common')
                avail_protos = []
                for f in sorted(os.listdir(common_dir)):
                    if f.endswith('_registers.py') and not f.startswith('REF_'):
                        proto = f.replace('_registers.py', '').split('_')[0].lower()
                        avail_protos.append((proto, f.replace('_registers.py', '')))

                print(f"\n  Available models ({len(avail_protos)}):")
                active_protos = {d['protocol'].lower() for d in config['devices']}
                for proto, fname in avail_protos:
                    active = '*' if proto in active_protos else ' '
                    print(f"    [{active}] {fname}")

                print(f"\n  Active devices ({len(config['devices'])} from rs485_ch1.ini):")
                for d in config['devices']:
                    fc = "FC04" if d['type'] == 'inverter' and d['protocol'].lower().startswith('kstar') else "FC03"
                    print(f"    [Slave {d['slave_id']:2d}] {d['type']:8s} {d['protocol']:12s} {d['name']} ({fc})")
                print()
            else:
                config = None
        except Exception as e:
            print(f"  rs485_ch1.ini load failed: {e}")
            config = None

        # Priority 3: Fallback to simulator_config.json
        if not config or not config.get('devices'):
            auto_cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'simulator_config.json')
            if os.path.isfile(auto_cfg):
                print(f"  Fallback: loading {auto_cfg}")
                try:
                    with open(auto_cfg, encoding='utf-8') as f:
                        config = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError) as e:
                    print(f"  [WARNING] Config load error: {e}")
                    config = None

    # Priority 4: Interactive setup
    if config is None or not config.get('devices'):
        config = _interactive_setup()

    # Override port/baudrate from CLI (with validation)
    if args.port:
        config['port'] = args.port
    if args.baudrate:
        if args.baudrate not in (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200):
            print(f"  [WARNING] Non-standard baudrate: {args.baudrate}")
        config['baudrate'] = args.baudrate

    # Ensure port/baudrate defaults
    if 'port' not in config:
        config['port'] = 'COM10'
    if 'baudrate' not in config:
        config['baudrate'] = 9600

    # COM 포트 확인/변경 (CLI --port 미지정 시)
    if not args.port:
        port_input = input(f"  COM Port [{config['port']}]: ").strip()
        if port_input:
            config['port'] = port_input

    simulator = EquipmentSimulator(config)
    simulator.start()


if __name__ == '__main__':
    main()
