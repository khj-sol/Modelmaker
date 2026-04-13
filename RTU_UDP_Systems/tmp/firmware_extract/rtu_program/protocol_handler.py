"""
Protocol Handler for RTU UDP Communication
Version: 1.0.1

UDP Version - Removed TCP framing, H07/H08 firmware update
All packet formats (H01~H06) are identical to TCP version
"""

import struct
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.protocol_constants import *


class ProtocolHandler:
    """Handle RTU UDP Protocol packets (No TCP framing)"""

    VERSION = "1.0.0"

    def __init__(self, rtu_id: int = 48810978):
        self.rtu_id = rtu_id
        import random
        self.sequence = random.randint(1, 65535)

    def _next_sequence(self):
        seq = self.sequence
        self.sequence = seq % 65535 + 1  # Wraps: 1→2→...→65535→1
        return seq

    # =========================================================================
    # Protocol Header Methods
    # =========================================================================

    def create_header(self, version, device_type, device_number,
                      device_model_number, body_type, backup_data=0, sequence=None):
        """Create 20-byte protocol header"""
        if sequence is None:
            sequence = self._next_sequence()
        timestamp = int(time.time())

        return struct.pack(HEADER_FORMAT,
            version, sequence, self.rtu_id, timestamp,
            device_type, device_number, device_model_number,
            backup_data, body_type
        )

    def parse_header(self, data):
        """Parse 20-byte protocol header"""
        if len(data) < HEADER_SIZE:
            return None
        try:
            v = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
            return {
                'version': v[0], 'sequence': v[1], 'rtu_id': v[2],
                'timestamp': v[3], 'device_type': v[4], 'device_number': v[5],
                'device_model_number': v[6], 'backup_data': v[7], 'body_type': v[8]
            }
        except Exception:
            return None

    # =========================================================================
    # H01 Packet Creation
    # =========================================================================

    def create_h01_inverter(self, device_number, device_model, data,
                            body_type=INV_BODY_BASIC_MPPT_STRING,
                            backup_data=0, sequence=None):
        """Create H01 Inverter packet"""
        if sequence is None:
            sequence = self._next_sequence()

        header = self.create_header(
            VERSION_H01, DEVICE_INVERTER, device_number,
            device_model, body_type, backup_data, sequence
        )

        basic = struct.pack(INV_BASIC_FORMAT,
            int(data.get('pv_voltage', 0)),
            int(data.get('pv_current', 0)),
            int(data.get('pv_power', 0)),
            int(data.get('r_voltage', 0)),
            int(data.get('s_voltage', 0)),
            int(data.get('t_voltage', 0)),
            int(data.get('r_current', 0)),
            int(data.get('s_current', 0)),
            int(data.get('t_current', 0)),
            int(data.get('ac_power', 0)),
            int(data.get('power_factor', 1000)),
            int(data.get('frequency', 600)),
            int(data.get('cumulative_energy', 0)),
            int(data.get('status', INV_STATUS_ON_GRID)),
            int(data.get('alarm1', 0)),
            int(data.get('alarm2', 0)),
            int(data.get('alarm3', 0))
        )

        body = basic

        if body_type in (INV_BODY_BASIC_MPPT, INV_BODY_BASIC_MPPT_STRING):
            mppt = data.get('mppt', [])[:255]  # Max 255 MPPT channels (1-byte count)
            mppt_body = struct.pack('>B', len(mppt))
            for m in mppt:
                mppt_body += struct.pack('>HH',
                    int(m.get('voltage', 0)),
                    int(m.get('current', 0) / 10))
            body += mppt_body

        if body_type in (INV_BODY_BASIC_STRING, INV_BODY_BASIC_MPPT_STRING):
            strings = data.get('strings', [])[:255]  # Max 255 strings (1-byte count)
            str_body = struct.pack('>B', len(strings))
            for s in strings:
                str_body += struct.pack('>H', int(s / 10))
            body += str_body


        return header + body, sequence

    def create_h01_relay(self, device_number, device_model, data, backup_data=0, sequence=None):
        """Create H01 Relay packet"""
        if sequence is None:
            sequence = self._next_sequence()

        header = self.create_header(
            VERSION_H01, DEVICE_PROTECTION_RELAY, device_number,
            device_model, RELAY_BODY_BASIC_DATA, backup_data, sequence
        )

        body = struct.pack(RELAY_BASIC_FORMAT,
            float(data.get('r_voltage', 380)),
            float(data.get('s_voltage', 380)),
            float(data.get('t_voltage', 380)),
            float(data.get('r_current', 0)),
            float(data.get('s_current', 0)),
            float(data.get('t_current', 0)),
            float(data.get('r_active_power', 0)),
            float(data.get('s_active_power', 0)),
            float(data.get('t_active_power', 0)),
            float(data.get('total_active_power', 0)),
            float(data.get('avg_power_factor', 1.0)),
            float(data.get('frequency', 60.0)),
            float(data.get('received_energy', 0)),
            float(data.get('sent_energy', 0)),
            int(data.get('do_status', 0)),
            int(data.get('di_status', 0))
        )

        return header + body, sequence

    def create_h01_weather(self, device_number, device_model, data, backup_data=0, sequence=None):
        """Create H01 Weather packet (SEM5046)"""
        if sequence is None:
            sequence = self._next_sequence()

        header = self.create_header(
            VERSION_H01, DEVICE_WEATHER_STATION, device_number,
            device_model, WEATHER_BODY_BASIC_DATA, backup_data, sequence
        )

        air_temp  = int(data.get('air_temp', 20) * 10)
        humidity  = int(data.get('air_humidity', 50) * 10)
        pressure  = int(data.get('air_pressure', 1013) * 10)
        wind_speed = int(data.get('wind_speed', 0) * 10)
        wind_dir  = int(data.get('wind_direction', 0))
        module1   = int(data.get('module_temp_1', 25) * 10)
        h_rad     = int(data.get('horizontal_radiation', 0))
        h_accum   = int(data.get('horizontal_accum', 0) * 100)
        i_rad     = int(data.get('inclined_radiation', 0))
        i_accum   = int(data.get('inclined_accum', 0) * 100)
        module2   = int(data.get('module_temp_2', 25) * 10)
        module3   = int(data.get('module_temp_3', 25) * 10)
        module4   = int(data.get('module_temp_4', 25) * 10)

        body = struct.pack(WEATHER_BASIC_FORMAT,
            air_temp, humidity, pressure, wind_speed, wind_dir,
            module1, h_rad, h_accum, i_rad, i_accum,
            module2, module3, module4
        )

        return header + body, sequence

    def create_h01_comm_fail(self, device_type, device_number, device_model,
                              body_type=-1, backup_data=0, sequence=None):
        """Create H01 communication failure packet (header only)"""
        if sequence is None:
            sequence = self._next_sequence()

        header = self.create_header(
            VERSION_H01, device_type, device_number,
            device_model, body_type, backup_data, sequence
        )

        return header, sequence

    def create_h01_nighttime_standby(self, device_type, device_number, device_model,
                                      backup_data=0, sequence=None):
        """Create H01 nighttime standby packet (header only, Kstar only, 20:00-05:00)"""
        if sequence is None:
            sequence = self._next_sequence()

        from common.protocol_constants import INV_BODY_NIGHTTIME
        header = self.create_header(
            VERSION_H01, device_type, device_number,
            device_model, INV_BODY_NIGHTTIME, backup_data, sequence
        )

        return header, sequence

    # =========================================================================
    # H02 ACK Packet
    # =========================================================================

    def create_h02(self, sequence, response=RESPONSE_SUCCESS):
        """Create H02 ACK"""
        return struct.pack(H02_FORMAT, VERSION_H02, sequence, response)

    def parse_h02(self, data):
        """Parse H02 ACK"""
        if len(data) < H02_SIZE:
            return None
        try:
            v = struct.unpack(H02_FORMAT, data[:H02_SIZE])
            return {'version': v[0], 'sequence': v[1], 'response': v[2]}
        except Exception:
            return None

    # =========================================================================
    # H03 Control Command
    # =========================================================================

    def parse_h03(self, data):
        """Parse H03 control command from server"""
        if len(data) < H03_SIZE:
            return None
        try:
            v = struct.unpack(H03_FORMAT, data[:H03_SIZE])
            return {
                'version': v[0], 'sequence': v[1], 'control_type': v[2],
                'device_type': v[3], 'device_number': v[4], 'control_value': v[5]
            }
        except Exception:
            return None

    # =========================================================================
    # H04 Control Response
    # =========================================================================

    def create_h04(self, sequence, control_type, device_type, device_number,
                   control_value, response=RESPONSE_SUCCESS):
        """Create H04 control response"""
        return struct.pack(H04_FORMAT,
            VERSION_H04, sequence, control_type, device_type,
            device_number, control_value, response)

    # =========================================================================
    # H05 Event / Heartbeat Packet
    # =========================================================================

    def create_h05_heartbeat(self, sequence=None):
        """Create H05 Heartbeat packet"""
        if sequence is None:
            sequence = self._next_sequence()

        header = self.create_header(
            VERSION_H05, DEVICE_RTU, 0, 0, BODY_TYPE_HEARTBEAT, 0, sequence
        )
        return header, sequence

    def create_h05_event(self, event_name, sequence=None):
        """Create H05 event packet"""
        if sequence is None:
            sequence = self._next_sequence()

        header = self.create_header(
            VERSION_H05, DEVICE_RTU, 0, 0, BODY_TYPE_RTU_EVENT, 0, sequence
        )
        event_bytes = event_name.encode('utf-8')
        body = struct.pack('>B', len(event_bytes)) + event_bytes
        return header + body, sequence

    def create_h05_rtu_info(self, rtu_info: dict, sequence=None):
        """Create H05 RTU Info packet"""
        if sequence is None:
            sequence = self._next_sequence()

        header = self.create_header(
            VERSION_H05, DEVICE_RTU, 0, 0, BODY_TYPE_RTU_INFO, 0, sequence
        )
        body = b''
        for field in ['model', 'phone', 'serial', 'firmware']:
            data = rtu_info.get(field, '').encode('utf-8')[:32]
            body += struct.pack('B', len(data)) + data
        return header + body, sequence

    def create_h05_rtu_event(self, event_msg: str, sequence=None):
        """Create H05 RTU Event packet"""
        if sequence is None:
            sequence = self._next_sequence()

        header = self.create_header(
            VERSION_H05, DEVICE_RTU, 0, 0, BODY_TYPE_RTU_EVENT, 0, sequence
        )
        data = event_msg.encode('utf-8')[:32]
        body = struct.pack('B', len(data)) + data
        return header + body, sequence

    def create_h05_power_outage(self, timestamp=None, sequence=None):
        """Create H05 Power Outage Event packet"""
        if sequence is None:
            sequence = self._next_sequence()
        if timestamp is None:
            timestamp = int(time.time())

        header = self.create_header(
            VERSION_H05, DEVICE_RTU, 0, 0, BODY_TYPE_POWER_OUTAGE, 0, sequence
        )
        body = struct.pack('>II', timestamp, 0)
        return header + body, sequence

    def create_h05_power_restore(self, outage_timestamp, restore_timestamp=None, sequence=None):
        """Create H05 Power Restore Event packet"""
        if sequence is None:
            sequence = self._next_sequence()
        if restore_timestamp is None:
            restore_timestamp = int(time.time())

        header = self.create_header(
            VERSION_H05, DEVICE_RTU, 0, 0, BODY_TYPE_POWER_RESTORE, 0, sequence
        )
        duration = restore_timestamp - outage_timestamp
        body = struct.pack('>III', outage_timestamp, restore_timestamp, duration)
        return header + body, sequence

    # =========================================================================
    # H05 Control Check / Control Result
    # =========================================================================

    def create_h05_control_check(self, device_number, model, control_data, sequence=None):
        """Create H05 Control Check packet (body type 13).

        Args:
            control_data: dict with on_off, power_factor, operation_mode,
                          reactive_power_pct, active_power_pct
        """
        if sequence is None:
            sequence = self._next_sequence()
        header = self.create_header(
            VERSION_H05, DEVICE_INVERTER, device_number, model,
            BODY_TYPE_CONTROL_CHECK, 0, sequence
        )
        body = struct.pack('>HhHhH',
            int(control_data.get('on_off', 0)),
            int(control_data.get('power_factor', 1000)),
            int(control_data.get('operation_mode', 0)),
            int(control_data.get('reactive_power_pct', 0)),
            int(control_data.get('active_power_pct', 1000)),
        )
        return header + body, sequence

    def create_h05_control_result(self, device_number, model, monitor_data, sequence=None):
        """Create H05 Control Result packet (body type 14).

        Args:
            monitor_data: dict with current_r/s/t, voltage_rs/st/tr,
                          active_power_kw, reactive_power_var, power_factor,
                          frequency, status_flags
        """
        if sequence is None:
            sequence = self._next_sequence()
        header = self.create_header(
            VERSION_H05, DEVICE_INVERTER, device_number, model,
            BODY_TYPE_CONTROL_RESULT, 0, sequence
        )
        body = struct.pack('>IIIIIIiiii',
            int(monitor_data.get('current_r', 0) * 10),
            int(monitor_data.get('current_s', 0) * 10),
            int(monitor_data.get('current_t', 0) * 10),
            int(monitor_data.get('voltage_rs', 0) * 10),
            int(monitor_data.get('voltage_st', 0) * 10),
            int(monitor_data.get('voltage_tr', 0) * 10),
            int(monitor_data.get('active_power_kw', 0) * 10),
            int(monitor_data.get('reactive_power_var', 0)),
            int(monitor_data.get('power_factor', 1.0) * 1000),
            int(monitor_data.get('frequency', 60.0) * 10),
        )
        body += struct.pack('>I', monitor_data.get('status_flags', 0))
        return header + body, sequence

    def create_h05_inverter_model(self, device_number, model, model_info, sequence=None):
        """Create H05 Inverter Model Info packet (body type 11)."""
        if sequence is None:
            sequence = self._next_sequence()
        header = self.create_header(
            VERSION_H05, DEVICE_INVERTER, device_number, model,
            BODY_TYPE_INVERTER_MODEL, 0, sequence
        )
        body = b''
        field_keys = [('model_name', 'model'), ('serial_number', 'serial')]
        for primary, fallback in field_keys:
            data = (model_info.get(primary) or model_info.get(fallback, '')).encode('utf-8')[:32]
            body += struct.pack('B', len(data)) + data
        # Capability flags: bit0=iv_scan, bit1=der_avm
        cap = model_info.get('capabilities', 0)
        body += struct.pack('B', cap)
        return header + body, sequence

    # =========================================================================
    # H05 IV Scan
    # =========================================================================

    def create_h05_iv_scan_success(self, device_number, model, sequence=None):
        """Create H05 IV Scan Success packet (body type 12, header only)."""
        if sequence is None:
            sequence = self._next_sequence()
        header = self.create_header(
            VERSION_H05, DEVICE_INVERTER, device_number, model,
            BODY_TYPE_IV_SCAN_SUCCESS, 0, sequence
        )
        return header, sequence

    def create_h05_iv_scan_data(self, device_number, model,
                                 string_num, total_strings, data_points,
                                 sequence=None):
        """Create H05 IV Scan Data packet (body type 15).

        Args:
            string_num: 1-based string number
            total_strings: total number of strings
            data_points: list of (voltage, current) tuples (already scaled)
        """
        if sequence is None:
            sequence = self._next_sequence()
        header = self.create_header(
            VERSION_H05, DEVICE_INVERTER, device_number, model,
            BODY_TYPE_IV_SCAN_DATA, 0, sequence
        )
        body = struct.pack('>BBB', total_strings, string_num, len(data_points))
        for v, i in data_points:
            body += struct.pack('>HH', int(v), int(i))
        return header + body, sequence

    # =========================================================================
    # H06 ACK for H05
    # =========================================================================

    def parse_h06(self, data):
        """Parse H06 ACK for H05"""
        if len(data) < H06_SIZE:
            return None
        try:
            v = struct.unpack(H06_FORMAT, data[:H06_SIZE])
            return {'version': v[0], 'sequence': v[1], 'response': v[2]}
        except Exception:
            return None

    # =========================================================================
    # H07 Firmware Update (Server → RTU)
    # =========================================================================

    def parse_h07(self, data):
        """Parse H07 Firmware Update Request.

        Packet: Version(1) + Seq(2) + DevType(1) + DevNum(1)
                + FTP_Host(1+N) + FTP_Port(2) + FTP_User(1+N)
                + FTP_Pass(1+N) + FTP_Path(1+N) + FTP_Filename(1+N)
        """
        if len(data) < 5:
            return None
        try:
            pos = 0
            version = data[pos]; pos += 1
            sequence = struct.unpack('>H', data[pos:pos+2])[0]; pos += 2
            dev_type = data[pos]; pos += 1
            dev_num = data[pos]; pos += 1

            def read_string():
                nonlocal pos
                slen = data[pos]; pos += 1
                s = data[pos:pos+slen].decode('utf-8', errors='ignore')
                pos += slen
                return s

            ftp_host = read_string()
            ftp_port = struct.unpack('>H', data[pos:pos+2])[0]; pos += 2
            ftp_user = read_string()
            ftp_password = read_string()
            ftp_path = read_string()
            ftp_filename = read_string()

            return {
                'sequence': sequence,
                'device_type': dev_type,
                'device_number': dev_num,
                'ftp_host': ftp_host,
                'ftp_port': ftp_port,
                'ftp_user': ftp_user,
                'ftp_password': ftp_password,
                'ftp_path': ftp_path,
                'ftp_filename': ftp_filename,
            }
        except Exception:
            return None

    # =========================================================================
    # H08 Firmware Update Response (RTU → Server)
    # =========================================================================

    def create_h08(self, sequence, response):
        """Create H08 Firmware Update Response packet."""
        return struct.pack(H08_FORMAT,
            VERSION_H08, sequence, DEVICE_RTU, 0xFF, response)
