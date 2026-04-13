#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UDP Test Server for RTU Protocol V2.0.10
Full Control Support with Built-in FTP Server
Version: 1.0.7

Version 1.0.1:
- Version sync with RTU system V1.0.1

Version 1.0.0 - Unified Release:
- All components unified to V1.0.0
- Built-in FTP server using pyftpdlib
- H07 firmware update command support
- Backup data time display as info (not ERROR)
- Full inverter/relay control support
- IV Scan, Power Factor, Active/Reactive Power control
- RTU Reboot command support
"""

import socket
import struct
import time
import threading
import logging
import argparse
import signal
import sys
import os
from datetime import datetime, timezone, timedelta

# FTP Server imports
try:
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
    FTP_AVAILABLE = True
except ImportError:
    FTP_AVAILABLE = False
    print("WARNING: pyftpdlib not installed. FTP server feature disabled.")
    print("         Install with: pip install pyftpdlib")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol_constants import *

# KST Timezone (UTC+9)
KST = timezone(timedelta(hours=9))


def utc_to_kst(utc_timestamp):
    """Convert UTC Unix timestamp to KST datetime string"""
    utc_dt = datetime.fromtimestamp(utc_timestamp, tz=timezone.utc)
    kst_dt = utc_dt.astimezone(KST)
    return kst_dt.strftime('%Y-%m-%d %H:%M:%S')


def utc_to_kst_short(utc_timestamp):
    """Convert UTC Unix timestamp to KST time string (short format)"""
    utc_dt = datetime.fromtimestamp(utc_timestamp, tz=timezone.utc)
    kst_dt = utc_dt.astimezone(KST)
    return kst_dt.strftime('%H:%M:%S')


def get_local_ip():
    """Get local IP address for external connections"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


class FTPServerManager:
    """Built-in FTP Server Manager using pyftpdlib"""
    
    def __init__(self, port: int = DEFAULT_BUILTIN_FTP_PORT, 
                 root_dir: str = DEFAULT_FTP_ROOT_DIR):
        self.port = port
        self.root_dir = os.path.abspath(root_dir)
        self.server = None
        self.server_thread = None
        self.running = False
        self.authorizer = None
        self.users = {}  # username: password
        
        # Default user
        self.users[DEFAULT_FTP_USER] = DEFAULT_FTP_PASSWORD
        
        # Setup logging
        self.logger = logging.getLogger('FTPServer')
    
    def _ensure_root_dir(self):
        """Create root directory if not exists"""
        if not os.path.exists(self.root_dir):
            os.makedirs(self.root_dir)
            self.logger.info(f"Created FTP root directory: {self.root_dir}")
    
    def add_user(self, username: str, password: str, perm: str = "elradfmw"):
        """Add FTP user"""
        self.users[username] = password
        if self.authorizer:
            try:
                self.authorizer.add_user(username, password, self.root_dir, perm=perm)
            except ValueError:
                self.authorizer.remove_user(username)
                self.authorizer.add_user(username, password, self.root_dir, perm=perm)
        self.logger.info(f"Added/Updated FTP user: {username}")
    
    def remove_user(self, username: str):
        """Remove FTP user"""
        if username in self.users:
            del self.users[username]
            if self.authorizer:
                try:
                    self.authorizer.remove_user(username)
                except KeyError:
                    pass
            self.logger.info(f"Removed FTP user: {username}")
    
    def start(self) -> bool:
        """Start FTP server in background thread"""
        if not FTP_AVAILABLE:
            self.logger.error("pyftpdlib not installed")
            return False
        
        if self.running:
            self.logger.warning("FTP server already running")
            return True
        
        try:
            self._ensure_root_dir()
            
            self.authorizer = DummyAuthorizer()
            for username, password in self.users.items():
                self.authorizer.add_user(username, password, self.root_dir, perm="elradfmw")
            
            handler = FTPHandler
            handler.authorizer = self.authorizer
            handler.passive_ports = range(60000, 60100)
            handler.banner = "RTU UDP Test Server FTP Service Ready"
            
            self.server = FTPServer(("0.0.0.0", self.port), handler)
            self.server.max_cons = 10
            self.server.max_cons_per_ip = 5
            
            self.server_thread = threading.Thread(target=self._run_server, daemon=True)
            self.server_thread.start()
            
            self.running = True
            self.logger.info(f"FTP server started on port {self.port}")
            self.logger.info(f"FTP root directory: {self.root_dir}")
            return True
            
        except OSError as e:
            if e.errno == 10048 or e.errno == 98:
                self.logger.error(f"Port {self.port} already in use (FileZilla running?)")
            else:
                self.logger.error(f"Failed to start FTP server: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Failed to start FTP server: {e}")
            return False
    
    def _run_server(self):
        """Run FTP server (called in thread)"""
        try:
            self.server.serve_forever()
        except Exception as e:
            if self.running:
                self.logger.error(f"FTP server error: {e}")
    
    def stop(self):
        """Stop FTP server"""
        if not self.running:
            return
        
        self.running = False
        if self.server:
            try:
                self.server.close_all()
            except:
                pass
            self.server = None
        
        self.logger.info("FTP server stopped")
    
    def is_running(self) -> bool:
        """Check if FTP server is running"""
        return self.running
    
    def get_status(self) -> dict:
        """Get FTP server status"""
        return {
            'running': self.running,
            'port': self.port,
            'root_dir': self.root_dir,
            'users': list(self.users.keys()),
            'local_ip': get_local_ip()
        }


class UDPTestServer:
    """UDP Test Server for RTU Communication - Full Control Support"""
    
    VERSION = "1.0.7"
    
    def __init__(self, listen_port: int = DEFAULT_SERVER_PORT,
                 rtu_port: int = DEFAULT_RTU_LOCAL_PORT,
                 ftp_port: int = DEFAULT_BUILTIN_FTP_PORT,
                 ftp_root: str = DEFAULT_FTP_ROOT_DIR):
        self.listen_port = listen_port
        self.rtu_port = rtu_port
        self.socket = None
        self.running = False
        self.sequence = 1000
        
        self.rtu_addresses = {}
        self.rtu_last_seen = {}
        self._socket_lock = threading.Lock()
        
        self.stats = {
            'h01_received': 0,
            'h02_sent': 0,
            'h03_sent': 0,
            'h04_received': 0,
            'h05_received': 0,
            'h06_sent': 0,
            'iv_scan_data': 0
        }
        
        self.ftp_manager = FTPServerManager(port=ftp_port, root_dir=ftp_root)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%H:%M:%S'
        )
        self.logger = logging.getLogger('Server')
    
    def start(self):
        """Start the server"""
        print("=" * 70)
        print(f"  UDP Test Server v{self.VERSION}")
        print(f"  RTU Protocol: {PROTOCOL_VERSION}")
        print("=" * 70)
        print(f"  UDP Listen Port: {self.listen_port}")
        print(f"  RTU Port: {self.rtu_port}")
        if FTP_AVAILABLE:
            print(f"  FTP Server Port: {self.ftp_manager.port}")
            print(f"  FTP Root Dir: {self.ftp_manager.root_dir}")
        else:
            print("  FTP Server: NOT AVAILABLE (install pyftpdlib)")
        print("=" * 70)
        print()
        
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(('0.0.0.0', self.listen_port))
        self.socket.settimeout(0.5)
        
        self.running = True
        
        receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
        receive_thread.start()
        
        self.logger.info(f"Server started on port {self.listen_port}")
        self.logger.info("Waiting for RTU connections...\n")

        if getattr(self, '_headless', False):
            # Headless mode: no interactive menu, just wait
            try:
                while self.running:
                    time.sleep(1)
            except (KeyboardInterrupt, EOFError):
                self.stop()
        else:
            self._interactive_menu()
    
    def stop(self):
        """Stop the server"""
        self.running = False
        self.ftp_manager.stop()
        if self.socket:
            self.socket.close()
        print("\nServer stopped")
    
    def _receive_loop(self):
        """Receive packets"""
        while self.running:
            try:
                data, addr = self.socket.recvfrom(4096)
                self._handle_packet(data, addr)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    self.logger.error(f"Receive error: {e}")
    
    def _handle_packet(self, data: bytes, addr: tuple):
        """Handle incoming packet"""
        if len(data) < 1:
            return
        
        version = data[0]
        ts = datetime.now().strftime('%H:%M:%S')
        
        if version == VERSION_H01:
            self._handle_h01(data, addr, ts)
        elif version == VERSION_H04:
            self._handle_h04(data, addr, ts)
        elif version == VERSION_H05:
            self._handle_h05(data, addr, ts)
        elif version == VERSION_H08:
            self._handle_h08(data, addr, ts)
    
    def _handle_h01(self, data: bytes, addr: tuple, ts: str):
        """Handle H01 periodic data"""
        if len(data) < HEADER_SIZE:
            return

        header = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
        rtu_id = header[2]
        rtu_timestamp = header[3]
        seq = header[1]
        dev_type = header[4]
        dev_num = header[5]
        model = header[6]
        backup = header[7]
        body_type = header[8]

        self.rtu_addresses[rtu_id] = addr
        self.rtu_last_seen[rtu_id] = time.time()
        self.stats['h01_received'] += 1

        # Send ACK first (before display) to minimize RTU wait time
        ack = struct.pack(H02_FORMAT, VERSION_H02, seq, RESPONSE_SUCCESS)
        with self._socket_lock:
            self.socket.sendto(ack, addr)
        self.stats['h02_sent'] += 1

        # Display after ACK sent — exceptions here won't affect ACK delivery
        try:
            dev_name = DEVICE_TYPE_NAMES.get(dev_type, f"Type{dev_type}")
            backup_flag = " [BACKUP]" if backup else ""

            rtu_time_kst = utc_to_kst_short(rtu_timestamp)

            server_utc = int(time.time())
            time_diff = rtu_timestamp - server_utc

            # Time validation differs for current vs backup data
            if backup:
                age_seconds = abs(time_diff)
                if age_seconds < 60:
                    time_status = f"BACKUP: {age_seconds}s ago"
                elif age_seconds < 3600:
                    time_status = f"BACKUP: {age_seconds//60}m {age_seconds%60}s ago"
                else:
                    time_status = f"BACKUP: {age_seconds//3600}h {(age_seconds%3600)//60}m ago"
            else:
                if abs(time_diff) <= 5:
                    time_status = "OK"
                elif abs(time_diff) <= 60:
                    time_status = f"WARN: {time_diff:+d}s"
                else:
                    time_status = f"ERROR: {time_diff:+d}s ({abs(time_diff)//60}m)"

            if body_type < 0:
                body_type_names = {
                    -3: "ZEE_SKIP",
                    -2: "PACKET_ERROR",
                    -1: "COMM_FAIL"
                }
                fail_name = body_type_names.get(body_type, f"ERROR({body_type})")
                print(f"\n[{ts}] H01 from {addr[0]}:{addr[1]}{backup_flag} [!!! {fail_name} !!!]")
                print(f"   RTU:{rtu_id} | {dev_name}{dev_num} | Model:{model} | Seq:{seq}")
                print(f"   RTU Time: {rtu_time_kst} KST (ts={rtu_timestamp}) [{time_status}]")
                print(f"   >>> COMMUNICATION FAILURE (Body Type: {body_type}) - Header Only <<<")
            else:
                print(f"\n[{ts}] H01 from {addr[0]}:{addr[1]}{backup_flag}")
                print(f"   RTU:{rtu_id} | {dev_name}{dev_num} | Model:{model} | Seq:{seq}")
                print(f"   RTU Time: {rtu_time_kst} KST (ts={rtu_timestamp}) [{time_status}]")

                if dev_type == DEVICE_INVERTER:
                    self._display_inverter(data)
                elif dev_type == DEVICE_PROTECTION_RELAY:
                    self._display_relay(data)

            print(f"   Sent H02 ACK (seq={seq})")
        except Exception as e:
            print(f"   [!] Display error (ACK already sent): {e}")
    
    def _display_inverter(self, data: bytes):
        """Display inverter data"""
        if len(data) < HEADER_SIZE + INV_BASIC_SIZE:
            return
        
        body = data[HEADER_SIZE:]
        basic = struct.unpack(INV_BASIC_FORMAT, body[:INV_BASIC_SIZE])
        
        pv_v, pv_c, pv_p = basic[0], basic[1], basic[2]
        r_v, s_v, t_v = basic[3], basic[4], basic[5]
        r_c, s_c, t_c = basic[6], basic[7], basic[8]
        ac_p = basic[9]
        pf = basic[10] / 1000.0
        freq = basic[11] / 10.0
        energy = basic[12]
        status = basic[13]
        
        status_name = INV_STATUS_NAMES.get(status, f"0x{status:04X}")
        
        print(f"   PV: {pv_v}V x {pv_c}A = {pv_p}W")
        print(f"   AC: R={r_v}V S={s_v}V T={t_v}V | {r_c}A {s_c}A {t_c}A")
        print(f"   Power:{ac_p}W | PF:{pf:.3f} | Freq:{freq:.1f}Hz | Status:{status_name}")
        print(f"   Energy: {energy}Wh ({energy/1000:.1f}kWh)")
        
        offset = INV_BASIC_SIZE
        if len(body) > offset:
            mppt_count = body[offset]
            offset += 1
            if mppt_count > 0:
                mppts = []
                for i in range(mppt_count):
                    if offset + 4 <= len(body):
                        v, c = struct.unpack('>HH', body[offset:offset+4])
                        mppts.append(f"CH{i+1}:{v/10:.0f}V/{c/10:.1f}A")
                        offset += 4
                print(f"   MPPT: {', '.join(mppts)}")
        
        if len(body) > offset:
            str_count = body[offset]
            offset += 1
            if str_count > 0:
                strs = []
                for i in range(str_count):
                    if offset + 2 <= len(body):
                        c = struct.unpack('>H', body[offset:offset+2])[0]
                        strs.append(f"{c/10:.1f}A")
                        offset += 2
                print(f"   Strings({str_count}): {', '.join(strs)}")
    
    def _display_relay(self, data: bytes):
        """Display relay data"""
        if len(data) < HEADER_SIZE + RELAY_BASIC_SIZE:
            return
        
        body = data[HEADER_SIZE:]
        vals = struct.unpack(RELAY_BASIC_FORMAT, body[:RELAY_BASIC_SIZE])
        
        print(f"   V: R={vals[0]:.1f} S={vals[1]:.1f} T={vals[2]:.1f}")
        print(f"   I: R={vals[3]:.2f} S={vals[4]:.2f} T={vals[5]:.2f}")
        print(f"   Total:{vals[9]:.0f}W | PF:{vals[10]:.3f} | Freq:{vals[11]:.2f}Hz")
    
    def _handle_h04(self, data: bytes, addr: tuple, ts: str):
        """Handle H04 response"""
        if len(data) < H04_SIZE:
            return
        
        vals = struct.unpack(H04_FORMAT, data[:H04_SIZE])
        seq, ctrl_type, dev_type, dev_num, ctrl_val, resp = vals[1:7]
        
        ctrl_name = CONTROL_TYPE_NAMES.get(ctrl_type, f"Type{ctrl_type}")
        resp_str = "SUCCESS" if resp == 0 else "FAILED"
        detail = ""
        if ctrl_type == CTRL_INV_ON_OFF:
            detail = " ON" if ctrl_val == 0 else " OFF"
        elif ctrl_type == CTRL_INV_ACTIVE_POWER:
            detail = f" {ctrl_val/10:.1f}%"
        elif ctrl_type == CTRL_INV_POWER_FACTOR:
            detail = f" PF={ctrl_val/1000:.3f}"
        elif ctrl_type == CTRL_INV_REACTIVE_POWER:
            detail = f" {ctrl_val/10:.1f}%"

        self.stats['h04_received'] += 1
        print(f"\n[{ts}] H04 Response: INV{dev_num} {ctrl_name}{detail} -> {resp_str}")
    
    def _handle_h05(self, data: bytes, addr: tuple, ts: str):
        """Handle H05 event/response"""
        if len(data) < HEADER_SIZE:
            return

        header = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
        rtu_id = header[2]
        seq = header[1]
        body_type = header[8]
        dev_num = header[5]

        self.rtu_addresses[rtu_id] = addr
        self.stats['h05_received'] += 1

        # Send ACK first before display
        ack = struct.pack(H06_FORMAT, VERSION_H06, seq, RESPONSE_SUCCESS)
        with self._socket_lock:
            self.socket.sendto(ack, addr)
        self.stats['h06_sent'] += 1

        # Display after ACK sent
        try:
            body = data[HEADER_SIZE:]

            print(f"\n[{ts}] H05 from RTU:{rtu_id} (BodyType:{body_type})")

            if body_type == BODY_TYPE_HEARTBEAT:
                print(f"   Heartbeat PING")

            elif body_type == BODY_TYPE_RTU_EVENT:
                if len(body) > 0:
                    event_len = body[0]
                    if len(body) > event_len:
                        event_name = body[1:1+event_len].decode('utf-8', errors='ignore')
                        print(f"   Event: \"{event_name}\"")

            elif body_type == BODY_TYPE_RTU_INFO:
                pos = 0
                fields = ['Model', 'Phone', 'Serial', 'Firmware']
                for field_name in fields:
                    if pos < len(body):
                        size = body[pos]
                        pos += 1
                        if pos + size <= len(body):
                            value = body[pos:pos+size].decode('utf-8', errors='ignore')
                            print(f"   {field_name}: {value}")
                            pos += size

            elif body_type == BODY_TYPE_POWER_OUTAGE:
                if len(body) >= 4:
                    outage_time = struct.unpack('>I', body[:4])[0]
                    print(f"   *** POWER OUTAGE DETECTED ***")
                    print(f"   Time: {utc_to_kst(outage_time)} KST")

            elif body_type == BODY_TYPE_POWER_RESTORE:
                if len(body) >= 12:
                    outage_time, restore_time, duration = struct.unpack('>III', body[:12])
                    print(f"   *** POWER RESTORED ***")
                    print(f"   Outage: {utc_to_kst(outage_time)} KST")
                    print(f"   Restore: {utc_to_kst(restore_time)} KST")
                    print(f"   Duration: {duration}s")

            elif body_type == BODY_TYPE_INVERTER_MODEL:
                pos = 0
                fields = ['Model', 'Serial']
                for field_name in fields:
                    if pos < len(body):
                        size = body[pos]
                        pos += 1
                        if pos + size <= len(body):
                            value = body[pos:pos+size].decode('utf-8', errors='ignore')
                            print(f"   {field_name}: {value}")
                            pos += size

            elif body_type == BODY_TYPE_CONTROL_CHECK:
                if len(body) >= 10:
                    run_stop, pf, op_mode, rp_pct, ap_pct = struct.unpack('>HhHHH', body[:10])
                    print(f"   INV{dev_num} Control Status:")
                    print(f"     Run/Stop: {'Running' if run_stop == 0 else 'Stopped'}")
                    print(f"     Power Factor: {pf/1000:.3f}")
                    print(f"     Operation Mode: {op_mode}")
                    print(f"     Reactive Power: {rp_pct/10:.1f}%")
                    print(f"     Active Power: {ap_pct/10:.1f}%")

            elif body_type == BODY_TYPE_CONTROL_RESULT:
                if len(body) >= 44:
                    values = struct.unpack('>IIIIIIiiiII', body[:44])
                    i_r, i_s, i_t = values[0]/10, values[1]/10, values[2]/10
                    v_rs, v_st, v_tr = values[3]/10, values[4]/10, values[5]/10
                    p_active = values[6]/10
                    p_reactive = values[7]
                    pf = values[8]/1000
                    freq = values[9]/10
                    flags = values[10]

                    print(f"   INV{dev_num} Power Monitoring:")
                    print(f"     Current: R={i_r:.1f}A, S={i_s:.1f}A, T={i_t:.1f}A")
                    print(f"     Voltage: RS={v_rs:.1f}V, ST={v_st:.1f}V, TR={v_tr:.1f}V")
                    print(f"     Active Power: {p_active:.1f}kW")
                    print(f"     Reactive Power: {p_reactive}Var")
                    print(f"     Power Factor: {pf:.3f}")
                    print(f"     Frequency: {freq:.1f}Hz")
                    print(f"     Status Flags: 0x{flags:08X}")

            elif body_type == BODY_TYPE_IV_SCAN_SUCCESS:
                print(f"   IV Scan Complete (header only)")

            elif body_type == BODY_TYPE_IV_SCAN_DATA:
                if len(body) >= 3:
                    total_str, str_num, points = struct.unpack('>BBB', body[:3])
                    print(f"   IV Scan Data: String {str_num}/{total_str}, {points} points")
                    self.stats['iv_scan_data'] += 1
                    if len(body) >= 3 + points * 4:
                        for i in range(min(3, points)):
                            v, c = struct.unpack('>HH', body[3+i*4:7+i*4])
                            print(f"     Point {i+1}: {v/10:.1f}V, {c/100:.2f}A")
                        if points > 3:
                            print(f"     ... ({points-3} more points)")

            print(f"   Sent H06 ACK (seq={seq})")
        except Exception as e:
            print(f"   [!] Display error (ACK already sent): {e}")
    
    def _send_h03(self, rtu_ip: str, ctrl_type: int, dev_type: int,
                  dev_num: int, value: int):
        """Send H03 control"""
        self.sequence = (self.sequence % 65535) + 1
        
        packet = struct.pack(H03_FORMAT,
            VERSION_H03, self.sequence, ctrl_type, dev_type, dev_num, value)
        
        with self._socket_lock:
            self.socket.sendto(packet, (rtu_ip, self.rtu_port))
        self.stats['h03_sent'] += 1
        
        ctrl_name = CONTROL_TYPE_NAMES.get(ctrl_type, f"Type{ctrl_type}")
        detail = ""
        if ctrl_type == CTRL_INV_ON_OFF:
            detail = " (ON)" if value == 0 else " (OFF)"
        elif ctrl_type == CTRL_INV_ACTIVE_POWER:
            detail = f" ({value/10:.1f}%)"
        elif ctrl_type == CTRL_INV_POWER_FACTOR:
            detail = f" (PF={value/1000:.3f})"
        elif ctrl_type == CTRL_INV_REACTIVE_POWER:
            detail = f" ({value/10:.1f}%)"
        print(f"\nSent H03: {ctrl_name} -> INV{dev_num} Value:{value}{detail}")
    
    def _get_rtu_ip(self):
        """Get RTU IP"""
        if not self.rtu_addresses:
            print("No RTU connected. Waiting for H01 data from RTU...")
            return None
        if len(self.rtu_addresses) == 1:
            return list(self.rtu_addresses.values())[0][0]
        
        print("Connected RTUs:")
        for i, (rid, addr) in enumerate(self.rtu_addresses.items()):
            print(f"  [{i+1}] RTU {rid} at {addr[0]}")
        try:
            idx = int(input("Select: ")) - 1
            return list(self.rtu_addresses.values())[idx][0]
        except:
            return None
    
    def _handle_h08(self, data: bytes, addr: tuple, ts: str):
        """Handle H08 Firmware Update Response"""
        if len(data) < H08_SIZE:
            return
        
        try:
            version, seq, dev_type, dev_num, response = struct.unpack(H08_FORMAT, data[:H08_SIZE])
            
            response_names = {
                0: "SUCCESS (Update started)",
                1: "COMPLETE (Update applied, restarting)",
                -1: "ERROR (Packet error)",
                -2: "FTP_CONNECT_FAIL",
                -3: "FTP_LOGIN_FAIL",
                -4: "FTP_DOWNLOAD_FAIL",
                -5: "EXTRACT_FAIL",
                -6: "APPLY_FAIL",
                -7: "BUSY (Already updating)",
                -8: "HASH_FAIL",
            }

            resp_name = response_names.get(response, f"UNKNOWN({response})")

            print(f"\n[{ts}] H08 Response from {addr[0]}:{addr[1]}")
            print(f"   Seq: {seq}")
            print(f"   Device: Type={dev_type}, Num={dev_num}")
            print(f"   Response: {resp_name}")

            if response == 0:
                print("   >>> RTU is downloading and applying firmware update <<<")
            elif response == 1:
                print("   >>> Firmware update SUCCESS - RTU restarting <<<")
            elif response < 0:
                print(f"   >>> Update failed: {resp_name} <<<")
                
        except Exception as e:
            print(f"[{ts}] H08 parse error: {e}")
    
    def _send_h07(self, rtu_ip: str, ftp_host: str, ftp_port: int,
                  ftp_user: str, ftp_password: str, ftp_path: str, ftp_filename: str):
        """Send H07 Firmware Update Request"""
        self.sequence = (self.sequence % 65535) + 1
        
        packet = bytearray()
        
        packet.append(VERSION_H07)
        packet.extend(struct.pack('>H', self.sequence))
        packet.append(DEVICE_RTU)
        packet.append(0xFF)
        
        def add_string(s: str):
            data = s.encode('utf-8')[:64]
            packet.append(len(data))
            packet.extend(data)
        
        add_string(ftp_host)
        packet.extend(struct.pack('>H', ftp_port))
        add_string(ftp_user)
        add_string(ftp_password)
        add_string(ftp_path)
        add_string(ftp_filename)
        
        with self._socket_lock:
            self.socket.sendto(bytes(packet), (rtu_ip, self.rtu_port))

        print(f"\nSent H07 Firmware Update Request (seq={self.sequence})")
        print(f"   FTP: {ftp_user}@{ftp_host}:{ftp_port}")
        print(f"   Path: {ftp_path}/{ftp_filename}")
    
    def _ftp_server_menu(self):
        """FTP Server control submenu"""
        while True:
            status = self.ftp_manager.get_status()
            
            print("\n" + "=" * 60)
            print("  FTP Server Control")
            print("=" * 60)
            print(f"  Status: {'RUNNING' if status['running'] else 'STOPPED'}")
            print(f"  Port: {status['port']}")
            print(f"  Root Directory: {status['root_dir']}")
            print(f"  Local IP: {status['local_ip']}")
            print(f"  Users: {', '.join(status['users'])}")
            print("-" * 60)
            print("  [1] Start FTP Server")
            print("  [2] Stop FTP Server")
            print("  [3] Add/Update User")
            print("  [4] Remove User")
            print("  [5] Open Root Directory")
            print("  [0] Back to Main Menu")
            print("-" * 60)
            
            choice = input("Select: ").strip()
            
            if choice == '0':
                break
            elif choice == '1':
                if not FTP_AVAILABLE:
                    print("\nERROR: pyftpdlib not installed")
                    print("Install with: pip install pyftpdlib")
                elif self.ftp_manager.start():
                    print("\nFTP Server started successfully")
                    print(f"Connect to: ftp://{get_local_ip()}:{self.ftp_manager.port}")
                else:
                    print("\nFailed to start FTP server")
                    print("Check if port 21 is in use (FileZilla Server?)")
            elif choice == '2':
                self.ftp_manager.stop()
                print("\nFTP Server stopped")
            elif choice == '3':
                username = input("Username: ").strip()
                if username:
                    password = input("Password: ").strip()
                    self.ftp_manager.add_user(username, password)
                    print(f"\nUser '{username}' added/updated")
            elif choice == '4':
                username = input("Username to remove: ").strip()
                if username:
                    self.ftp_manager.remove_user(username)
                    print(f"\nUser '{username}' removed")
            elif choice == '5':
                root_dir = self.ftp_manager.root_dir
                if not os.path.exists(root_dir):
                    os.makedirs(root_dir)
                if sys.platform == 'win32':
                    os.startfile(root_dir)
                    print(f"\nOpened: {root_dir}")
                else:
                    print(f"\nFTP Root Directory: {root_dir}")
    
    def _firmware_update_menu(self, rtu_ip: str):
        """Firmware update submenu"""
        print("\n" + "=" * 60)
        print("  Firmware Update")
        print("=" * 60)
        
        ftp_status = self.ftp_manager.get_status()
        
        if ftp_status['running']:
            default_host = ftp_status['local_ip']
            default_port = ftp_status['port']
            default_user = DEFAULT_FTP_USER
            default_password = DEFAULT_FTP_PASSWORD
            print(f"  [Built-in FTP Server ACTIVE]")
        else:
            default_host = DEFAULT_FTP_HOST
            default_port = DEFAULT_FTP_PORT
            default_user = "rtu"
            default_password = ""
            print(f"  [Using External FTP: {default_host}]")
            print(f"  TIP: Start built-in FTP server with menu [F]")
        
        default_path = "/"

        # Auto-detect firmware files in FTP root
        fw_files = []
        ftp_root = self.ftp_manager.root_dir if ftp_status['running'] else './firmware'
        if os.path.isdir(ftp_root):
            fw_files = [f for f in os.listdir(ftp_root) if f.endswith('.tar.gz')]
        default_filename = fw_files[0] if fw_files else "rtu_firmware.tar.gz"

        print(f"  Default FTP: {default_user}@{default_host}:{default_port}")
        if fw_files:
            print(f"  Available files: {', '.join(fw_files)}")
        print(f"  Default File: {default_filename}")
        print()

        ftp_host = input(f"FTP Host [{default_host}]: ").strip() or default_host
        ftp_port = int(input(f"FTP Port [{default_port}]: ").strip() or default_port)
        ftp_user = input(f"FTP User [{default_user}]: ").strip() or default_user
        ftp_password = input(f"FTP Password: ").strip() or default_password
        ftp_path = input(f"FTP Path [{default_path}]: ").strip() or default_path
        ftp_filename = input(f"Filename [{default_filename}]: ").strip() or default_filename
        
        print()
        print(f"  Target: {ftp_user}@{ftp_host}:{ftp_port}{ftp_path}/{ftp_filename}")
        confirm = input("Send update request? [y/N]: ").strip().lower()
        
        if confirm == 'y':
            self._send_h07(rtu_ip, ftp_host, ftp_port, ftp_user, ftp_password, ftp_path, ftp_filename)
        else:
            print("Cancelled.")
    
    def _interactive_menu(self):
        """Interactive menu with full control options"""
        while self.running:
            try:
                ftp_status = "ON" if self.ftp_manager.is_running() else "OFF"
                
                print("\n" + "=" * 60)
                print("  RTU Control Menu")
                print("=" * 60)
                print("  [1] Inverter ON/OFF")
                print("  [2] Active Power Limit")
                print("  [3] Power Factor")
                print("  [4] Reactive Power")
                print("  [5] Control Init (Reset)")
                print("  [6] Control Status Check")
                print("  [7] Model Info Query")
                print("  [8] IV Scan")
                print("  [9] RTU Info")
                print("  [R] RTU Reboot")
                print("  [U] Firmware Update")
                print("-" * 60)
                print(f"  [F] FTP Server Control [{ftp_status}]")
                print("  [S] Show Stats")
                print("  [Q] Quit")
                print("-" * 60)
                
                choice = input("Select: ").strip().lower()
                
                if choice == 'q':
                    self.stop()
                    break
                elif choice == '1':
                    ip = self._get_rtu_ip()
                    if ip:
                        dev = int(input("Inverter num (1-10): "))
                        val = int(input("Value (0=ON, 1=OFF): "))
                        self._send_h03(ip, CTRL_INV_ON_OFF, DEVICE_INVERTER, dev, val)
                elif choice == '2':
                    ip = self._get_rtu_ip()
                    if ip:
                        dev = int(input("Inverter num: "))
                        pct = float(input("Power limit % (0-100): "))
                        self._send_h03(ip, CTRL_INV_ACTIVE_POWER, DEVICE_INVERTER, dev, int(pct*10))
                elif choice == '3':
                    ip = self._get_rtu_ip()
                    if ip:
                        dev = int(input("Inverter num: "))
                        pf = float(input("Power factor (-1.0~1.0): "))
                        self._send_h03(ip, CTRL_INV_POWER_FACTOR, DEVICE_INVERTER, dev, int(pf*1000))
                elif choice == '4':
                    ip = self._get_rtu_ip()
                    if ip:
                        dev = int(input("Inverter num: "))
                        rp = float(input("Reactive power % (-100.0~100.0): "))
                        self._send_h03(ip, CTRL_INV_REACTIVE_POWER, DEVICE_INVERTER, dev, int(rp*10))
                elif choice == '5':
                    ip = self._get_rtu_ip()
                    if ip:
                        dev = int(input("Inverter num: "))
                        self._send_h03(ip, CTRL_INV_CONTROL_INIT, DEVICE_INVERTER, dev, 0)
                elif choice == '6':
                    ip = self._get_rtu_ip()
                    if ip:
                        dev = int(input("Inverter num: "))
                        self._send_h03(ip, CTRL_INV_CONTROL_CHECK, DEVICE_INVERTER, dev, 0)
                elif choice == '7':
                    ip = self._get_rtu_ip()
                    if ip:
                        dev = int(input("Inverter num: "))
                        self._send_h03(ip, CTRL_INV_MODEL, DEVICE_INVERTER, dev, 0)
                elif choice == '8':
                    ip = self._get_rtu_ip()
                    if ip:
                        dev = int(input("Inverter num: "))
                        self._send_h03(ip, CTRL_INV_IV_SCAN, DEVICE_INVERTER, dev, 1)
                elif choice == '9':
                    ip = self._get_rtu_ip()
                    if ip:
                        self._send_h03(ip, CTRL_RTU_INFO, DEVICE_RTU, 0, 0)
                elif choice == 'r':
                    ip = self._get_rtu_ip()
                    if ip and input("Confirm RTU Reboot? (yes/no): ") == 'yes':
                        self._send_h03(ip, CTRL_RTU_REBOOT, DEVICE_RTU, 0, 0)
                elif choice == 'u':
                    ip = self._get_rtu_ip()
                    if ip:
                        self._firmware_update_menu(ip)
                elif choice == 'f':
                    self._ftp_server_menu()
                elif choice == 's':
                    print("\n  Statistics:")
                    for k, v in self.stats.items():
                        print(f"    {k}: {v}")
                    print(f"\n  FTP Server: {ftp_status}")
                    if self.ftp_manager.is_running():
                        print(f"    Local IP: {get_local_ip()}")
                        print(f"    Port: {self.ftp_manager.port}")
                        
            except KeyboardInterrupt:
                self.stop()
                break
            except Exception as e:
                print(f"Error: {e}")


def main():
    parser = argparse.ArgumentParser(description='UDP Test Server with Built-in FTP')
    parser.add_argument('--port', type=int, default=DEFAULT_SERVER_PORT,
                        help=f'UDP listen port (default: {DEFAULT_SERVER_PORT})')
    parser.add_argument('--rtu-port', type=int, default=DEFAULT_RTU_LOCAL_PORT,
                        help=f'RTU port (default: {DEFAULT_RTU_LOCAL_PORT})')
    parser.add_argument('--ftp-port', type=int, default=DEFAULT_BUILTIN_FTP_PORT,
                        help=f'FTP server port (default: {DEFAULT_BUILTIN_FTP_PORT})')
    parser.add_argument('--ftp-root', type=str, default=DEFAULT_FTP_ROOT_DIR,
                        help=f'FTP root directory (default: {DEFAULT_FTP_ROOT_DIR})')
    parser.add_argument('--headless', action='store_true',
                        help='Run without interactive menu (for background/automated use)')

    args = parser.parse_args()

    server = UDPTestServer(
        listen_port=args.port,
        rtu_port=args.rtu_port,
        ftp_port=args.ftp_port,
        ftp_root=args.ftp_root
    )
    
    server._headless = args.headless

    signal.signal(signal.SIGINT, lambda s, f: server.stop())

    server.start()


if __name__ == '__main__':
    main()
