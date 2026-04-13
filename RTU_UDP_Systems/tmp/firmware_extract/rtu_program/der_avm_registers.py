#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DER-AVM Register Map Definitions
Version: 1.0.6

DER-AVM Master connects to RTU via RS485 HAT CH2
- Reads real-time data every 1 minute
- Sends control commands to RTU
- RTU forwards control to all inverters
"""


class DerAvmRegisters:
    """DER-AVM Modbus Register Addresses"""
    
    # =========================================================================
    # Real-time Data Registers (Read Only) - Function Code 03
    # Address range: 0x03E8 ~ 0x03FD (1000 ~ 1021)
    # All values are S32 (2 registers each)
    # =========================================================================
    
    L1_CURRENT = 0x03E8      # S32, scale 0.1A
    L2_CURRENT = 0x03EA      # S32, scale 0.1A
    L3_CURRENT = 0x03EC      # S32, scale 0.1A
    L1_VOLTAGE = 0x03EE      # S32, scale 0.1V
    L2_VOLTAGE = 0x03F0      # S32, scale 0.1V
    L3_VOLTAGE = 0x03F2      # S32, scale 0.1V
    ACTIVE_POWER = 0x03F4    # S32, scale 0.1kW (L1+L2+L3 total)
    REACTIVE_POWER = 0x03F6  # S32, unit Var (L1+L2+L3 total)
    POWER_FACTOR = 0x03F8    # S32, scale 0.001
    FREQUENCY = 0x03FA       # S32, scale 0.1Hz
    STATUS_FLAG = 0x03FC     # S32, bitmap
    
    # Real-time data register range
    REALTIME_START = 0x03E8
    REALTIME_END = 0x03FD
    REALTIME_COUNT = 22      # 11 x S32 = 22 registers
    
    # =========================================================================
    # Control Parameter Registers (Read/Write) - Function Code 03/06/16
    # =========================================================================
    
    POWER_FACTOR_SET = 0x07D0      # S16, scale 0.001, range [-1000,-800] or [800,1000]
    ACTION_MODE = 0x07D1           # U16, 0:Self, 2:DER-AVM, 5:Q(V)
    REACTIVE_POWER_PCT = 0x07D2    # S16, scale 0.1%, range [-484, 484]
    ACTIVE_POWER_PCT = 0x07D3      # U16, scale 0.1%, range [0, 1100]
    INVERTER_OFF = 0x0834          # U16, 0:On, 1:Off
    
    # Control register range
    CONTROL_START_1 = 0x07D0
    CONTROL_END_1 = 0x07D3
    CONTROL_COUNT_1 = 4
    
    CONTROL_START_2 = 0x0834
    CONTROL_END_2 = 0x0834
    CONTROL_COUNT_2 = 1


class DerAvmActionMode:
    """Action Mode values for register 0x07D1"""
    SELF_CONTROL = 0
    DER_AVM_CONTROL = 2
    QV_CONTROL = 5


class DerAvmStatusFlag:
    """
    Status Flag bitmap (0x03FC~0x03FD)
    
    Bit definitions:
    - Bit 1: Inverter action state, 0=Run, 1=Fail
    - Bit 2: Inverter CB action state, 0=Run, 1=Fail
    - Bit 3: Run state, 0=DER-AVM control, 1=Self control
    - Bit 4: Active power control ACK (read-clear)
    - Bit 5: Inverter action control ACK (read-clear)
    """
    
    BIT_INV_ACTION = 0x01       # Bit 1: Inverter action state
    BIT_INV_CB_ACTION = 0x02    # Bit 2: CB action state
    BIT_RUN_STATE = 0x04        # Bit 3: Run state (0=DER-AVM, 1=Self)
    BIT_ACTIVE_PWR_ACK = 0x08   # Bit 4: Active power control ACK
    BIT_INV_ACTION_ACK = 0x10   # Bit 5: Inverter action control ACK
    
    def __init__(self):
        self._flags = 0x00  # All OK, DER-AVM control mode
        self._inv_fail_count = 0
        self._cb_fail_count = 0
    
    def update_inverter_status(self, all_ok: bool):
        """Update inverter action state (bit 1)"""
        if all_ok:
            self._flags &= ~self.BIT_INV_ACTION
            self._inv_fail_count = 0
        else:
            self._flags |= self.BIT_INV_ACTION
            self._inv_fail_count += 1
    
    def update_cb_status(self, all_ok: bool):
        """Update CB action state (bit 2)"""
        if all_ok:
            self._flags &= ~self.BIT_INV_CB_ACTION
            self._cb_fail_count = 0
        else:
            self._flags |= self.BIT_INV_CB_ACTION
            self._cb_fail_count += 1
    
    def set_run_state(self, is_self_control: bool):
        """Set run state (bit 3): 0=DER-AVM, 1=Self"""
        if is_self_control:
            self._flags |= self.BIT_RUN_STATE
        else:
            self._flags &= ~self.BIT_RUN_STATE
    
    def set_active_power_ack(self):
        """Set bit 4 when active power control received successfully"""
        self._flags |= self.BIT_ACTIVE_PWR_ACK
    
    def set_inverter_action_ack(self):
        """Set bit 5 when inverter on/off control received successfully"""
        self._flags |= self.BIT_INV_ACTION_ACK
    
    def read_and_clear_acks(self) -> int:
        """
        Read status flag value and clear ACK bits (bit 4, 5)
        Called when DER-AVM reads status flag register
        """
        value = self._flags
        # Clear ACK bits after read
        self._flags &= ~(self.BIT_ACTIVE_PWR_ACK | self.BIT_INV_ACTION_ACK)
        return value
    
    @property
    def value(self) -> int:
        """Get current flag value without clearing"""
        return self._flags
    
    def get_status_string(self) -> str:
        """Get human-readable status string"""
        parts = []
        if self._flags & self.BIT_INV_ACTION:
            parts.append("INV_FAIL")
        if self._flags & self.BIT_INV_CB_ACTION:
            parts.append("CB_FAIL")
        if self._flags & self.BIT_RUN_STATE:
            parts.append("SELF_CTRL")
        else:
            parts.append("DER_AVM_CTRL")
        if self._flags & self.BIT_ACTIVE_PWR_ACK:
            parts.append("PWR_ACK")
        if self._flags & self.BIT_INV_ACTION_ACK:
            parts.append("INV_ACK")
        return "|".join(parts) if parts else "OK"


# Default control values
DEFAULT_POWER_FACTOR = 1000       # 1.000
DEFAULT_ACTION_MODE = DerAvmActionMode.DER_AVM_CONTROL
DEFAULT_REACTIVE_POWER_PCT = 0    # 0%
DEFAULT_ACTIVE_POWER_PCT = 1000   # 100.0%
DEFAULT_INVERTER_OFF = 0          # On

# Communication timeout (seconds)
DER_AVM_COMM_TIMEOUT = 60  # 1 minute - if no read request, communication error
