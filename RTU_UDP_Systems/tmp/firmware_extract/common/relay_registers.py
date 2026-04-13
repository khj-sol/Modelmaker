"""
KDU-300 Protection Relay Modbus Register Map
Version: 1.0.7

Register Type: Input Register (Function Code 04)
Address: Actual Address (0-based), NOT Register Address (30001-based)

Conversion: Actual Address = Register Address - 30001

Data Format:
- float: 2 registers (4 bytes), Big Endian
- WORD: 1 register (2 bytes)

Changes in 1.0.7:
- Fixed DI1/DI2 register addresses (97->98, 98->99)
- Added TOTAL_REGISTERS constant
- Added register count for float/word types
"""

import struct


class KDU300RegisterMap:
    """KDU-300 Protection Relay Modbus Register Map"""
    
    # =========================================================================
    # Line Voltage (선간전압)
    # =========================================================================
    V12 = 0             # 30001, float, V
    V23 = 2             # 30003, float, V
    V31 = 4             # 30005, float, V
    
    # =========================================================================
    # Phase Voltage (상전압)
    # =========================================================================
    V1 = 6              # 30007, float, V (R phase)
    V2 = 8              # 30009, float, V (S phase)
    V3 = 10             # 30011, float, V (T phase)
    
    # =========================================================================
    # Current (전류)
    # =========================================================================
    A1 = 12             # 30013, float, A (R phase)
    A2 = 14             # 30015, float, A (S phase)
    A3 = 16             # 30017, float, A (T phase)
    
    # =========================================================================
    # Active Power (유효전력)
    # =========================================================================
    W1 = 18             # 30019, float, W (R phase)
    W2 = 20             # 30021, float, W (S phase)
    W3 = 22             # 30023, float, W (T phase)
    TOTAL_W = 24        # 30025, float, W (Total)
    
    # =========================================================================
    # Reactive Power (무효전력)
    # =========================================================================
    VAR1 = 26           # 30027, float, Var (R phase)
    VAR2 = 28           # 30029, float, Var (S phase)
    VAR3 = 30           # 30031, float, Var (T phase)
    TOTAL_VAR = 32      # 30033, float, Var (Total)
    
    # =========================================================================
    # Apparent Power (피상전력)
    # =========================================================================
    VA1 = 34            # 30035, float, VA (R phase)
    VA2 = 36            # 30037, float, VA (S phase)
    VA3 = 38            # 30039, float, VA (T phase)
    TOTAL_VA = 40       # 30041, float, VA (Total)
    
    # =========================================================================
    # Power Factor (역률)
    # =========================================================================
    PF1 = 42            # 30043, float (R phase)
    PF2 = 44            # 30045, float (S phase)
    PF3 = 46            # 30047, float (T phase)
    AVG_PF = 48         # 30049, float (Average)
    
    # =========================================================================
    # Frequency (주파수)
    # =========================================================================
    FREQUENCY = 50      # 30051, float, Hz
    
    # =========================================================================
    # Energy (전력량)
    # =========================================================================
    POSITIVE_WH = 52    # 30053, float, Wh (수전, received)
    NEGATIVE_WH = 54    # 30055, float, Wh (송전, sent)
    POSITIVE_VARH = 56  # 30057, float, Varh
    NEGATIVE_VARH = 58  # 30059, float, Varh
    
    # =========================================================================
    # Max Values (최대값) - Line Voltage
    # =========================================================================
    V12_MAX = 60        # 30061, float, V
    V23_MAX = 62        # 30063, float, V
    V31_MAX = 64        # 30065, float, V
    
    # =========================================================================
    # Max Values (최대값) - Phase Voltage
    # =========================================================================
    V1_MAX = 66         # 30067, float, V
    V2_MAX = 68         # 30069, float, V
    V3_MAX = 70         # 30071, float, V
    
    # =========================================================================
    # Max Values (최대값) - Current & Power
    # =========================================================================
    A1_MAX = 72         # 30073, float, A
    A2_MAX = 74         # 30075, float, A
    A3_MAX = 76         # 30077, float, A
    W_MAX = 78          # 30079, float, W
    
    # =========================================================================
    # Phase Angle (전압-전류간 위상)
    # =========================================================================
    P1_ANGLE = 80       # 30081, float, degrees
    P2_ANGLE = 82       # 30083, float, degrees
    P3_ANGLE = 84       # 30085, float, degrees
    
    # =========================================================================
    # Reverse Power (역전력)
    # =========================================================================
    REVERSE_WATT1 = 86  # 30087, float, W
    REVERSE_WATT2 = 88  # 30089, float, W
    REVERSE_WATT3 = 90  # 30091, float, W
    
    # =========================================================================
    # Protection Status (보호 상태) - WORD
    # =========================================================================
    DO_STATUS = 92      # 30093, WORD (DO output status)
    OVR = 93            # 30094, WORD (Over Voltage Relay)
    UVR = 94            # 30095, WORD (Under Voltage Relay)
    OFR = 95            # 30096, WORD (Over Frequency Relay)
    UFR = 96            # 30097, WORD (Under Frequency Relay)
    RPR = 97            # 30098, WORD (Reverse Power Relay)
    
    # =========================================================================
    # DI Status (DI 상태) - WORD
    # =========================================================================
    DI1 = 98            # 30099, WORD (DI1 status)
    DI2 = 99            # 30100, WORD (DI2 status)
    
    # =========================================================================
    # Register counts
    # =========================================================================
    TOTAL_REGISTERS = 100       # Total number of registers (0-99)
    FLOAT_REGISTER_COUNT = 46   # Number of float registers (2 regs each)
    WORD_REGISTER_COUNT = 8     # Number of WORD registers


def registers_to_float(reg_high, reg_low):
    """Convert two 16-bit registers to float (Big Endian)
    
    Args:
        reg_high: High word (first register)
        reg_low: Low word (second register)
    
    Returns:
        float value
    """
    # Pack as big-endian unsigned shorts, unpack as big-endian float
    data = struct.pack('>HH', reg_high, reg_low)
    return struct.unpack('>f', data)[0]


def float_to_registers(value):
    """Convert float to two 16-bit registers (Big Endian)
    
    Args:
        value: float value
    
    Returns:
        tuple: (reg_high, reg_low)
    """
    data = struct.pack('>f', value)
    return struct.unpack('>HH', data)


# H01 Body mapping for KDU-300
H01_RELAY_FIELD_MAP = {
    'r_voltage': KDU300RegisterMap.V1,          # 6
    's_voltage': KDU300RegisterMap.V2,          # 8
    't_voltage': KDU300RegisterMap.V3,          # 10
    'r_current': KDU300RegisterMap.A1,          # 12
    's_current': KDU300RegisterMap.A2,          # 14
    't_current': KDU300RegisterMap.A3,          # 16
    'r_active_power': KDU300RegisterMap.W1,     # 18
    's_active_power': KDU300RegisterMap.W2,     # 20
    't_active_power': KDU300RegisterMap.W3,     # 22
    'total_active_power': KDU300RegisterMap.TOTAL_W,  # 24
    'avg_power_factor': KDU300RegisterMap.AVG_PF,     # 48
    'frequency': KDU300RegisterMap.FREQUENCY,         # 50
    'received_energy': KDU300RegisterMap.POSITIVE_WH, # 52
    'sent_energy': KDU300RegisterMap.NEGATIVE_WH,     # 54
    'do_status': KDU300RegisterMap.DO_STATUS,         # 92
    'di_status': KDU300RegisterMap.DI1,               # 97
}
