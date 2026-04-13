# ============================================================================
# Huawei SUN2000 Modbus Register Map
# Protocol: huawei (Production)
# Based on huawei_mm_registers.py reference
# Date: 2026-03-29
# Protocol: Modbus RTU / TCP, Function Code 03 (Read Holding Registers)
# Byte order: Big-endian (MSB first)
# ============================================================================



# ============================================================================
# Register Address Constants
# ============================================================================

class RegisterMap:
    """Huawei SUN2000 Holding Register (FC03) address map"""

    # --- Device Status ---
    RUNNING_STATUS = 0x9186   # U16 — Running status
    FAULT_CODE_1 = 0x7D02   # U32 — Fault code 1 (2 regs: 32002-32003)
    FAULT_CODE_2 = 0x7D04   # U32 — Fault code 2 (2 regs: 32004-32005)

    # --- PV String Inputs (DC side) ---
    PV_STRING_BASE = 0x7D10   # First register of PV string block
    PV_STRING_COUNT = 16     # 16 registers total (8 strings x 2 regs each)

    # Individual PV string registers
    PV1_VOLTAGE = 0x7D10   # U16, 0.1V
    PV1_CURRENT = 0x7D11   # S16, 0.01A
    PV2_VOLTAGE = 0x7D12   # U16, 0.1V
    PV2_CURRENT = 0x7D13   # S16, 0.01A
    PV3_VOLTAGE = 0x7D14   # U16, 0.1V
    PV3_CURRENT = 0x7D15   # S16, 0.01A
    PV4_VOLTAGE = 0x7D16   # U16, 0.1V
    PV4_CURRENT = 0x7D17   # S16, 0.01A
    PV5_VOLTAGE = 0x7D18   # U16, 0.1V
    PV5_CURRENT = 0x7D19   # S16, 0.01A
    PV6_VOLTAGE = 0x7D1A   # U16, 0.1V
    PV6_CURRENT = 0x7D1B   # S16, 0.01A
    PV7_VOLTAGE = 0x7D1C   # U16, 0.1V
    PV7_CURRENT = 0x7D1D   # S16, 0.01A
    PV8_VOLTAGE = 0x7D1E   # U16, 0.1V
    PV8_CURRENT = 0x7D1F   # S16, 0.01A

    # --- DC Input Power ---
    INPUT_POWER = 0x7D40   # S32, 1W (2 regs: 32064-32065)

    # --- AC Grid (3-phase) ---
    PHASE_A_VOLTAGE = 0x7D45   # U16, 1V
    PHASE_B_VOLTAGE = 0x7D46   # U16, 1V
    PHASE_C_VOLTAGE = 0x7D47   # U16, 1V
    PHASE_A_CURRENT = 0x7D48   # S32, 0.001A (2 regs: 32072-32073)
    PHASE_B_CURRENT = 0x7D4A   # S32, 0.001A (2 regs: 32074-32075)
    PHASE_C_CURRENT = 0x7D4C   # S32, 0.001A (2 regs: 32076-32077)
    ACTIVE_POWER = 0x7D50   # S32, 1W (2 regs: 32080-32081)
    REACTIVE_POWER = 0x7D52   # S32, 1var (2 regs: 32082-32083)
    POWER_FACTOR = 0x7D54   # S16, 0.001
    GRID_FREQUENCY = 0x7D55   # U16, 0.01Hz

    # --- Temperature ---
    INTERNAL_TEMP = 0x7D57   # S16, 0.1C

    # --- Energy ---
    ACCUMULATED_ENERGY = 0x7D6A   # U32, 1kWh (2 regs: 32106-32107)

    # =========================================================================
    # DER-AVM Control Registers — FC03 Read / FC06 Write
    # =========================================================================

    DER_POWER_FACTOR_SET = 0x07D0   # S16, scale 0.001
    DER_ACTION_MODE = 0x07D1   # U16: 0=self, 2=DER-AVM, 5=Q(V)
    DER_REACTIVE_POWER_PCT = 0x07D2   # S16, scale 0.1%
    DER_ACTIVE_POWER_PCT = 0x07D3   # U16, scale 0.1%
    INVERTER_ON_OFF = 0x0834   # U16: 0=ON, 1=OFF

    # Alias (Solarize compatible)
    POWER_FACTOR_SET    = DER_POWER_FACTOR_SET
    ACTION_MODE         = DER_ACTION_MODE
    REACTIVE_POWER_PCT  = DER_REACTIVE_POWER_PCT
    OPERATION_MODE      = DER_ACTION_MODE
    REACTIVE_POWER_SET  = DER_REACTIVE_POWER_PCT
    ACTIVE_POWER_PCT    = DER_ACTIVE_POWER_PCT

    # =========================================================================
    # DEA-AVM Real-time Monitoring Data (0x03E8-0x03FD) - For H05 Body Type 14
    # =========================================================================

    DEA_L1_CURRENT_LOW = 0x03E8   # S32 low, scale 0.1A
    DEA_L1_CURRENT_HIGH = 0x03E9   # S32 high
    DEA_L2_CURRENT_LOW = 0x03EA   # S32 low, scale 0.1A
    DEA_L2_CURRENT_HIGH = 0x03EB   # S32 high
    DEA_L3_CURRENT_LOW = 0x03EC   # S32 low, scale 0.1A
    DEA_L3_CURRENT_HIGH = 0x03ED   # S32 high
    DEA_L1_VOLTAGE_LOW = 0x03EE   # S32 low, scale 0.1V
    DEA_L1_VOLTAGE_HIGH = 0x03EF   # S32 high
    DEA_L2_VOLTAGE_LOW = 0x03F0   # S32 low, scale 0.1V
    DEA_L2_VOLTAGE_HIGH = 0x03F1   # S32 high
    DEA_L3_VOLTAGE_LOW = 0x03F2   # S32 low, scale 0.1V
    DEA_L3_VOLTAGE_HIGH = 0x03F3   # S32 high
    DEA_TOTAL_ACTIVE_POWER_LOW = 0x03F4   # S32 low, scale 0.1kW
    DEA_TOTAL_ACTIVE_POWER_HIGH = 0x03F5   # S32 high
    DEA_TOTAL_REACTIVE_POWER_LOW = 0x03F6   # S32 low, scale 1 Var
    DEA_TOTAL_REACTIVE_POWER_HIGH = 0x03F7   # S32 high
    DEA_POWER_FACTOR_LOW = 0x03F8   # S32 low, scale 0.001
    DEA_POWER_FACTOR_HIGH = 0x03F9   # S32 high
    DEA_FREQUENCY_LOW = 0x03FA   # S32 low, scale 0.1Hz
    DEA_FREQUENCY_HIGH = 0x03FB   # S32 high
    DEA_STATUS_FLAG_LOW = 0x03FC   # U32 low, bit field
    DEA_STATUS_FLAG_HIGH = 0x03FD   # U32 high


    # =========================================================================
    # Standardized aliases (Solarize-compatible)
    # =========================================================================
    AC_POWER = ACTIVE_POWER
    R_PHASE_VOLTAGE = PHASE_A_VOLTAGE
    S_PHASE_VOLTAGE = PHASE_B_VOLTAGE
    T_PHASE_VOLTAGE = PHASE_C_VOLTAGE
    R_PHASE_CURRENT = PHASE_A_CURRENT
    S_PHASE_CURRENT = PHASE_B_CURRENT
    T_PHASE_CURRENT = PHASE_C_CURRENT
    FREQUENCY = GRID_FREQUENCY
    INNER_TEMP = INTERNAL_TEMP
    INVERTER_MODE = RUNNING_STATUS
    ERROR_CODE1 = FAULT_CODE_1
    ERROR_CODE2 = FAULT_CODE_2
    TOTAL_ENERGY = ACCUMULATED_ENERGY
    PV_POWER = INPUT_POWER

    # =========================================================================
    # MPPT aliases (derived from PV string pairs)
    # =========================================================================
    MPPT1_VOLTAGE = PV1_VOLTAGE
    MPPT1_CURRENT = PV1_CURRENT
    MPPT2_VOLTAGE = PV3_VOLTAGE
    MPPT2_CURRENT = PV3_CURRENT
    MPPT3_VOLTAGE = PV5_VOLTAGE
    MPPT3_CURRENT = PV5_CURRENT
    MPPT4_VOLTAGE = PV7_VOLTAGE
    MPPT4_CURRENT = PV7_CURRENT

    # =========================================================================
    # STRING aliases (PV string currents)
    # =========================================================================
    STRING1_CURRENT = PV1_CURRENT
    STRING2_CURRENT = PV2_CURRENT
    STRING3_CURRENT = PV3_CURRENT
    STRING4_CURRENT = PV4_CURRENT
    STRING5_CURRENT = PV5_CURRENT
    STRING6_CURRENT = PV6_CURRENT
    STRING7_CURRENT = PV7_CURRENT
    STRING8_CURRENT = PV8_CURRENT

    # --- Phase aliases (L1/L2/L3) ---
    L1_VOLTAGE = R_PHASE_VOLTAGE
    L2_VOLTAGE = S_PHASE_VOLTAGE
    L3_VOLTAGE = T_PHASE_VOLTAGE
    L1_CURRENT = R_PHASE_CURRENT
    L2_CURRENT = S_PHASE_CURRENT
    L3_CURRENT = T_PHASE_CURRENT

    # --- Energy aliases ---
    TOTAL_ENERGY_LOW = TOTAL_ENERGY
    TODAY_ENERGY_LOW = 0x7D6C  # virtual — Huawei has no daily energy register

    # --- DER-AVM control alias ---
    INVERTER_CONTROL = INVERTER_ON_OFF


# ============================================================================
# Scale Factors
# ============================================================================

SCALE = {
    'voltage': 0.1,
    'current': 0.01,
    'power': 1.0,
    'frequency': 0.01,
    'power_factor': 0.001,
}


# ============================================================================
# Solarize compatible (modbus_handler dynamic loading)
# ============================================================================

class InverterMode:
    """Solarize compatible InverterMode"""
    INITIAL = 0x00
    STANDBY = 0x01
    ON_GRID = 0x03
    OFF_GRID = 0x04
    FAULT = 0x05
    SHUTDOWN = 0x09

    @classmethod
    def to_string(cls, mode):
        mode_map = {
            0x00: "Initial", 0x01: "Standby", 0x03: "On-Grid",
            0x04: "Off-Grid", 0x05: "Fault", 0x09: "Shutdown"
        }
        return mode_map.get(mode, f"Unknown({mode})")


# ============================================================================
# Running Status -> InverterMode Converter
# ============================================================================

class HuaweiStatusConverter:
    """Convert Huawei running_status register value to InverterMode"""

    STATUS_STANDBY_INIT            = 0x0000  # Standby: initializing
    STATUS_STANDBY_INSULATION      = 0x0001  # Standby: insulation resistance detecting
    STATUS_STANDBY_SUNLIGHT        = 0x0002  # Standby: sunlight detecting
    STATUS_STANDBY_NETWORK         = 0x0003  # Standby: power network detecting
    STATUS_STARTING                = 0x0100  # Starting
    STATUS_ON_GRID                 = 0x0200  # On-grid (normal operation)
    STATUS_ON_GRID_DERATING        = 0x0201  # On-grid: derating due to power
    STATUS_ON_GRID_TEMP            = 0x0202  # On-grid: derating due to temperature
    STATUS_FAULT                   = 0x0300  # Shutdown: fault
    STATUS_SHUTDOWN_CMD            = 0x0301  # Shutdown: command
    STATUS_SHUTDOWN_OVGR           = 0x0302  # Shutdown: OVGR
    STATUS_SHUTDOWN_COMM           = 0x0303  # Shutdown: communication disconnected
    STATUS_SHUTDOWN_POWER          = 0x0304  # Shutdown: power limited
    STATUS_SHUTDOWN_MANUAL         = 0x0305  # Shutdown: manual startup required
    STATUS_SHUTDOWN_DC             = 0x0306  # Shutdown: DC switch disconnected

    _STATUS_NAMES = {
        0x0000: "Standby: initializing",
        0x0001: "Standby: insulation resistance detecting",
        0x0002: "Standby: sunlight detecting",
        0x0003: "Standby: power network detecting",
        0x0100: "Starting",
        0x0200: "On-grid (normal operation)",
        0x0201: "On-grid: derating due to power",
        0x0202: "On-grid: derating due to temperature",
        0x0300: "Shutdown: fault",
        0x0301: "Shutdown: command",
        0x0302: "Shutdown: OVGR",
        0x0303: "Shutdown: communication disconnected",
        0x0304: "Shutdown: power limited",
        0x0305: "Shutdown: manual startup required",
        0x0306: "Shutdown: DC switch disconnected",
    }

    @classmethod
    def to_inverter_mode(cls, raw_status: int) -> int:
        """Convert raw running_status to InverterMode code"""
        if raw_status == cls.STATUS_ON_GRID or raw_status == cls.STATUS_ON_GRID_DERATING \
                or raw_status == cls.STATUS_ON_GRID_TEMP:
            return InverterMode.ON_GRID
        elif raw_status == cls.STATUS_FAULT:
            return InverterMode.FAULT
        elif cls.STATUS_STANDBY_INIT <= raw_status <= cls.STATUS_STARTING \
                or cls.STATUS_SHUTDOWN_CMD <= raw_status <= cls.STATUS_SHUTDOWN_DC:
            return InverterMode.STANDBY
        else:
            return InverterMode.INITIAL

    @classmethod
    def to_string(cls, raw_status: int) -> str:
        """Get human-readable name for Huawei running status."""
        return cls._STATUS_NAMES.get(raw_status, f"Unknown({raw_status})")

    @classmethod
    def to_h01(cls, raw_status: int) -> int:
        """Alias for to_inverter_mode (used by modbus_handler)."""
        return cls.to_inverter_mode(raw_status)


# ============================================================================
# Helper Functions
# ============================================================================

def registers_to_s32(hi: int, lo: int) -> int:
    """Combine two U16 registers into a signed 32-bit integer (big-endian)"""
    val = ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)
    if val >= 0x80000000:
        val -= 0x100000000
    return val


def registers_to_u32(hi: int, lo: int) -> int:
    """Combine two U16 registers into an unsigned 32-bit integer (big-endian)"""
    return ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)


def s16(val: int) -> int:
    """Convert unsigned U16 register value to signed S16"""
    if val >= 0x8000:
        val -= 0x10000
    return val


def get_pv_string_data(regs: list) -> list:
    """
    Parse 16 consecutive registers (PV_STRING_BASE..+15) into per-string dicts.
    Layout: [V1, I1, V2, I2, ..., V8, I8]
    Returns list of 8 dicts: {'voltage': raw_U16, 'current': raw_S16}
    """
    result = []
    for i in range(8):
        v = regs[i * 2]          # U16, 0.1V
        c = s16(regs[i * 2 + 1]) # S16, 0.01A
        result.append({
            'voltage': max(0, v),
            'current': max(0, c),
        })
    return result


def get_mppt_from_strings(pv_data: list) -> list:
    """
    Derive 4 MPPT values from 8 PV string measurements.
    MPPT grouping: MPPT_n uses PV(2n-1) and PV(2n).
    """
    mppt = []
    for i in range(4):
        s1 = pv_data[i * 2]
        s2 = pv_data[i * 2 + 1]

        # Voltage: average of two strings (0.1V unit)
        if s1['voltage'] > 0 and s2['voltage'] > 0:
            mppt_v = (s1['voltage'] + s2['voltage']) // 2
        else:
            mppt_v = max(s1['voltage'], s2['voltage'])

        # Current: sum of two strings (0.01A unit)
        mppt_c = s1['current'] + s2['current']

        mppt.append({'voltage': mppt_v, 'current': mppt_c})
    return mppt


def get_string_currents(pv_data: list) -> list:
    """
    Extract string currents for H01 string array.
    Returns list of 8 raw current values (0.01A unit).
    """
    return [p['current'] for p in pv_data]


def get_cumulative_energy_wh(hi: int, lo: int) -> int:
    """
    Convert accumulated energy U32 register pair to Wh.
    Register unit: 1 kWh -> multiply x 1000 for Wh.
    """
    kwh = registers_to_u32(hi, lo)
    return kwh * 1000


def get_mppt_registers(mppt_num):
    """Get (voltage, current) register addresses for MPPT number (1-4)"""
    if mppt_num < 1 or mppt_num > 4:
        raise ValueError(f"MPPT number must be 1-4, got {mppt_num}")
    # MPPT1=PV1, MPPT2=PV3, MPPT3=PV5, MPPT4=PV7
    pv_idx = (mppt_num - 1) * 2  # 0, 2, 4, 6
    base = 0x7D10 + pv_idx * 2
    return (base, base + 1)


def get_string_registers(string_num):
    """Get (voltage, current) register addresses for string number (1-8)"""
    if string_num < 1 or string_num > 8:
        raise ValueError(f"String number must be 1-8, got {string_num}")
    base = 0x7D10 + (string_num - 1) * 2
    return (base, base + 1)


# Data type info per register
DATA_TYPES = {
    'PV_VOLTAGE': 'S16',
    'PV_CURRENT': 'S16',
    'PV_POWER': 'S32',
    'R_PHASE_VOLTAGE': 'U16',
    'S_PHASE_VOLTAGE': 'U16',
    'T_PHASE_VOLTAGE': 'U16',
    'R_PHASE_CURRENT': 'S32',
    'S_PHASE_CURRENT': 'S32',
    'T_PHASE_CURRENT': 'S32',
    'AC_POWER': 'S32',
    'POWER_FACTOR': 'S16',
    'FREQUENCY': 'U16',
    'TOTAL_ENERGY': 'U32',
    'INVERTER_MODE': 'U16',
    'ERROR_CODE1': 'U16',
    'ERROR_CODE2': 'U16',
    'INNER_TEMP': 'U16',
    'MPPT1_VOLTAGE': 'S16',
    'MPPT1_CURRENT': 'S16',
    'MPPT2_VOLTAGE': 'S16',
    'MPPT2_CURRENT': 'S16',
    'MPPT3_VOLTAGE': 'S16',
    'MPPT3_CURRENT': 'S16',
    'MPPT4_VOLTAGE': 'S16',
    'MPPT4_CURRENT': 'S16',
}

FLOAT32_FIELDS = set()


# Dynamic-loader alias
StatusConverter = HuaweiStatusConverter

# modbus_handler compatibility alias
HuaweiRegisters = RegisterMap
