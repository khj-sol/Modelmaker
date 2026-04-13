#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RTU UDP Client for CM4-ETH-RS485-BASE-B
Version: 1.1.0

Changes from TCP V3.0.0:
- TCP/TLS replaced with UDP (socket.SOCK_DGRAM)
- No TLS, no security authentication
- SimpleConfig replaces ConfigParser (no external dependency)
- Removed: firmware_updater, security_manager, pcap_uploader
- Kept: RS485/Modbus, backup_manager, der_avm_slave
- Send H01 data -> wait H02 ACK (retry on timeout)
- Send H05 heartbeat -> wait H06 ACK
- Background receive thread for H03 control commands
"""

import socket
import time
import threading
import logging
import argparse
import signal
import sys
import os
import struct
import configparser as _configparser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol_constants import *
from protocol_handler import ProtocolHandler
from modbus_handler import MultiDeviceHandler
from backup_manager import BackupManager
from der_avm_slave import DerAvmSlave, DerAvmSlaveSimulation
from der_avm_registers import DerAvmRegisters, DER_AVM_COMM_TIMEOUT
from common.solarize_registers import InverterMode, ErrorCode1, ErrorCode2, ErrorCode3

# ─── UDP Constants ────────────────────────────────────────────────────────────
UDP_RECV_TIMEOUT    = 10.0    # seconds to wait for ACK (H05 etc)
UDP_MAX_RETRIES     = 1       # send once, no retry (backup on fail)
UDP_SEND_INTERVAL   = 0.1     # seconds between device packets
HEARTBEAT_INTERVAL  = 30      # seconds between H05 heartbeats
DNS_REFRESH_INTERVAL = 60     # seconds between DNS re-resolve
BACKUP_SEND_INTERVAL = 60     # seconds between backup flush attempts
DEFAULT_UDP_PORT    = 9100
DEFAULT_SERVER_PORT = 9100
DEVICE_SEND_INTERVAL = 0.1   # seconds between H01 sends (batch)
BATCH_ACK_TIMEOUT   = 30     # seconds to wait for batch ACKs
FIRST_CONN_WAIT     = 60     # upper bound; actual wait uses configured comm_period
MODBUS_POLL_INTERVAL = 10    # seconds between Modbus polling cycles

# ─── Platform Detection ───────────────────────────────────────────────────────

def get_pi_model():
    try:
        with open('/proc/device-tree/model', 'r') as f:
            return f.read().rstrip('\x00')
    except Exception:
        return "Unknown"


def get_rtu_model_name():
    model = get_pi_model().lower()
    if 'compute module 4' in model or 'cm4' in model:
        return 'RTU-CM4'
    elif 'zero 2' in model:
        return 'RTU-PI0-2W'
    elif 'zero w' in model:
        return 'RTU-PI0-W'
    elif 'zero' in model:
        return 'RTU-PI0'
    elif 'pi 5' in model:
        return 'RTU-PI5'
    elif 'pi 4' in model:
        return 'RTU-PI4'
    elif 'pi 3' in model:
        return 'RTU-PI3'
    return 'RTU-PI'


def is_cm4_platform():
    model = get_pi_model().lower()
    return 'compute module 4' in model or 'cm4' in model


# ─── SimpleConfig ─────────────────────────────────────────────────────────────

class SimpleConfig:
    """
    Simple config loader for UDP RTU V1.0.0.
    Reads rtu_config.ini and rs485_ch1.ini directly.
    """

    def __init__(self, config_dir: str):
        self.config_dir = config_dir
        self.cfg = _configparser.ConfigParser()

        # Defaults
        self.server_mode = 'primary'
        self.primary_host = 'solarize.ddns.net'
        self.primary_port = DEFAULT_SERVER_PORT
        self.secondary_host = 'solar-on.duckdns.org'
        self.secondary_port = DEFAULT_SERVER_PORT
        self.rtu_id      = 10000001
        self.server_host = self.primary_host
        self.server_port = self.primary_port
        self.local_port  = DEFAULT_UDP_PORT
        self.comm_period = 60

    def load(self) -> bool:
        rtu_config_path = os.path.join(self.config_dir, 'rtu_config.ini')
        try:
            if not os.path.exists(rtu_config_path):
                logging.getLogger('RTU').error(f"Config not found: {rtu_config_path}")
                return False

            self.cfg.read(rtu_config_path)

            if self.cfg.has_section('RTU'):
                self.rtu_id     = self.cfg.getint('RTU', 'rtu_id', fallback=10000001)
                self.local_port = self.cfg.getint('RTU', 'local_port', fallback=DEFAULT_UDP_PORT)
                self.comm_period = self.cfg.getint('RTU', 'communication_period', fallback=60)

            if self.cfg.has_section('SERVER'):
                self.server_mode = self.cfg.get('SERVER', 'mode', fallback='primary').strip().lower()
                if self.server_mode not in ('primary', 'secondary', 'dual'):
                    logging.getLogger('RTU').warning(
                        f"Invalid SERVER.mode='{self.server_mode}', using 'primary'")
                    self.server_mode = 'primary'

                self.primary_host = self.cfg.get('SERVER', 'primary_host', fallback='solarize.ddns.net')
                self.primary_port = self.cfg.getint('SERVER', 'primary_port', fallback=DEFAULT_SERVER_PORT)
                self.secondary_host = self.cfg.get('SERVER', 'secondary_host', fallback=self.primary_host)
                self.secondary_port = self.cfg.getint('SERVER', 'secondary_port', fallback=self.primary_port)

                if self.server_mode == 'secondary':
                    self.server_host = self.secondary_host
                    self.server_port = self.secondary_port
                else:
                    # primary / dual: primary is main ACK target
                    self.server_host = self.primary_host
                    self.server_port = self.primary_port

            return True
        except Exception as e:
            logging.getLogger('RTU').error(f"Config load error: {e}")
            return False

    def get_rs485_config(self) -> dict:
        defaults = {'mode': 'auto', 'baudrate': 9600, 'serial_port': 'COM3'}
        if self.cfg.has_section('RS485'):
            return {
                'mode':        self.cfg.get('RS485', 'mode', fallback='auto'),
                'baudrate':    self.cfg.getint('RS485', 'baudrate', fallback=9600),
                'serial_port': self.cfg.get('RS485', 'serial_port', fallback='COM3'),
            }
        return defaults

    def get_der_avm_config(self) -> dict:
        if self.cfg.has_section('DER_AVM'):
            return {
                'enabled':        self.cfg.getboolean('DER_AVM', 'enabled', fallback=False),
                'simulation':     self.cfg.getboolean('DER_AVM', 'simulation', fallback=False),
                'slave_id':       self.cfg.getint('DER_AVM', 'slave_id', fallback=1),
                'channel':        self.cfg.getint('DER_AVM', 'channel', fallback=2),
                'baudrate':       self.cfg.getint('DER_AVM', 'baudrate', fallback=9600),
                'inverter_count': self.cfg.getint('DER_AVM', 'inverter_count', fallback=1),
            }
        return {'enabled': False}

    def print_config(self):
        logger = logging.getLogger('RTU')
        logger.info(f"  RTU ID     : {self.rtu_id}")
        logger.info(f"  Server Mode: {self.server_mode}")
        logger.info(f"  Primary    : {self.primary_host}:{self.primary_port}")
        if self.server_mode in ('secondary', 'dual'):
            logger.info(f"  Secondary  : {self.secondary_host}:{self.secondary_port}")
        logger.info(f"  Active     : {self.server_host}:{self.server_port}")
        logger.info(f"  Local Port : {self.local_port}")
        logger.info(f"  Comm Period: {self.comm_period}s")


# ─── RTU UDP Client ───────────────────────────────────────────────────────────

class RTUClient:
    """RTU UDP Client - Simple UDP protocol, RS485/Modbus unchanged"""

    VERSION = "1.1.0"

    def __init__(self, config: SimpleConfig = None, simulation_mode: bool = False):

        if config:
            self.rtu_id      = config.rtu_id
            self.server_mode = getattr(config, 'server_mode', 'primary')
            self.primary_host = getattr(config, 'primary_host', config.server_host)
            self.primary_port = getattr(config, 'primary_port', config.server_port)
            self.secondary_host = getattr(config, 'secondary_host', config.server_host)
            self.secondary_port = getattr(config, 'secondary_port', config.server_port)
            self.server_host = config.server_host
            self.server_port = config.server_port
            self.local_port  = config.local_port
            self.comm_period = config.comm_period
            self.config      = config
        else:
            self.rtu_id      = 10000001
            self.server_mode = 'primary'
            self.primary_host = 'localhost'
            self.primary_port = DEFAULT_SERVER_PORT
            self.secondary_host = 'localhost'
            self.secondary_port = DEFAULT_SERVER_PORT
            self.server_host = 'localhost'
            self.server_port = DEFAULT_SERVER_PORT
            self.local_port  = DEFAULT_UDP_PORT
            self.comm_period = 60
            self.config      = None

        self.simulation_mode = simulation_mode
        self.logger = logging.getLogger('RTU')

        # Platform
        self.is_cm4 = is_cm4_platform()
        if self.is_cm4:
            self.logger.info("CM4 platform detected - using native UART RS485")

        # RS485 setup
        rs485_cfg      = config.get_rs485_config() if config else {}
        rs485_mode     = rs485_cfg.get('mode', 'auto')
        rs485_baudrate = rs485_cfg.get('baudrate', 9600)
        rs485_serial   = rs485_cfg.get('serial_port', 'COM3')

        if rs485_mode == 'serial':
            use_hat    = False
            use_cm4_hw = False
        else:
            use_hat    = True
            use_cm4_hw = self.is_cm4

        self.protocol = ProtocolHandler(self.rtu_id)
        self.modbus = MultiDeviceHandler(
            use_hat=use_hat,
            channel=1,
            baudrate=rs485_baudrate,
            simulation_mode=simulation_mode,
            use_cm4=use_cm4_hw,
            serial_port=rs485_serial,
        )
        self.backup = BackupManager(self.rtu_id)

        # ── UDP socket ──────────────────────────────────────────────────────
        self.udp_socket   = None
        self.server_addr  = (self.server_host, self.server_port)
        self.primary_addr = (self.primary_host, self.primary_port)
        self.secondary_addr = (self.secondary_host, self.secondary_port)
        self.socket_lock  = threading.Lock()
        self._last_dns_refresh = 0

        # ── State ────────────────────────────────────────────────────────────
        self.running           = False
        self.server_reachable  = False
        self._ack_event        = threading.Event()

        self.inverters      = []
        self.relays         = []
        self.weather_sensors = []
        self.control_values = {}
        self._control_lock  = threading.Lock()
        self._modbus_lock   = threading.Lock()
        # Modbus polling: last successful data per device
        # key: ('inv', device_number) or ('relay', device_number) or ('weather', device_number)
        self._last_device_data = {}
        self._poll_lock = threading.Lock()

        # Recovery mode
        self.recovery_mode       = False
        self.recovery_wait_until = 0
        self._batch_receiving    = False  # True during batch ACK receive
        self._batch_ack_queue    = []     # H02 ACKs caught by _receive_loop during batch
        self._batch_queue_lock   = threading.Lock()
        self._recv_loop_yielded  = threading.Event()  # Set when _receive_loop is idle
        self._recv_loop_yielded.set()
        self._updating           = False  # True during firmware update

        # Heartbeat
        self.last_heartbeat_time = time.time()

        # DER-AVM
        self.der_avm_enabled = False
        self.der_avm         = None
        self.der_avm_data    = {}

        if config:
            der_cfg = config.get_der_avm_config()
            self.der_avm_enabled = der_cfg.get('enabled', False)
            if self.der_avm_enabled:
                if der_cfg.get('simulation', False):
                    self.der_avm = DerAvmSlaveSimulation(
                        slave_id=der_cfg.get('slave_id', 1),
                        inverter_count=der_cfg.get('inverter_count', 1)
                    )
                else:
                    self.der_avm = DerAvmSlave(
                        slave_id=der_cfg.get('slave_id', 1),
                        channel=der_cfg.get('channel', 2),
                        baudrate=der_cfg.get('baudrate', 9600),
                        inverter_count=der_cfg.get('inverter_count', 1),
                        use_cm4=self.is_cm4,
                        cm4_slave_port='/dev/ttyAMA1'
                    )

    def _recovery_wait_seconds(self) -> int:
        """Startup/recovery wait should not exceed the configured send period."""
        try:
            return max(0, min(int(self.comm_period), FIRST_CONN_WAIT))
        except Exception:
            return FIRST_CONN_WAIT

    # =========================================================================
    # UDP Socket Management
    # =========================================================================

    def _create_udp_socket(self) -> bool:
        """Create and bind UDP socket"""
        try:
            with self.socket_lock:
                if self.udp_socket:
                    try:
                        self.udp_socket.close()
                    except OSError:
                        pass
                    self.udp_socket = None

                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(0.5)  # Short timeout for responsive batch switching

                # Bind local port for receiving
                try:
                    sock.bind(('', self.local_port))
                    self.logger.info(f"UDP socket bound to port {self.local_port}")
                except OSError:
                    sock.bind(('', 0))
                    self.logger.warning("Local port in use, using random port")

                self.udp_socket = sock

            self._refresh_dns()
            return True

        except Exception as e:
            self.logger.error(f"Failed to create UDP socket: {e}")
            return False

    def _refresh_dns(self):
        """Resolve configured server hostnames to IP."""
        now = time.time()
        if now - self._last_dns_refresh < DNS_REFRESH_INTERVAL:
            return

        targets = [('primary', self.primary_host, self.primary_port)]
        if self.server_mode in ('secondary', 'dual'):
            targets.append(('secondary', self.secondary_host, self.secondary_port))

        for label, host, port in targets:
            try:
                ip = socket.gethostbyname(host)
                addr = (ip, port)
                self.logger.info(f"DNS[{label}]: {host} -> {ip}:{port}")
            except socket.gaierror as e:
                self.logger.error(f"DNS[{label}] resolution failed: {host} - {e}")
                addr = (host, port)

            if label == 'primary':
                self.primary_addr = addr
            else:
                self.secondary_addr = addr

        # Main ACK target: primary for dual mode
        if self.server_mode == 'secondary':
            self.server_host = self.secondary_host
            self.server_port = self.secondary_port
            self.server_addr = self.secondary_addr
        else:
            self.server_host = self.primary_host
            self.server_port = self.primary_port
            self.server_addr = self.primary_addr

        self._last_dns_refresh = now

    def _target_addrs(self):
        """Return destination list according to SERVER.mode."""
        if self.server_mode == 'secondary':
            return [('secondary', self.secondary_addr)]
        if self.server_mode == 'dual':
            return [('primary', self.primary_addr), ('secondary', self.secondary_addr)]
        return [('primary', self.primary_addr)]

    # =========================================================================
    # UDP Send / Receive
    # =========================================================================

    def _send_udp(self, packet: bytes) -> bool:
        """Send UDP packet and wait for ACK (H02 or H06).

        Returns True if ACK received within retries.
        ACK is signalled by _receive_loop() via _ack_event.
        """
        if not self.udp_socket:
            return False

        self._refresh_dns()

        for label, addr in self._target_addrs():
            try:
                self._ack_event.clear()
                t0 = time.time()
                with self.socket_lock:
                    self.udp_socket.sendto(packet, addr)

                # Wait for ACK from _receive_loop() thread
                if self._ack_event.wait(timeout=UDP_RECV_TIMEOUT):
                    elapsed_ms = (time.time() - t0) * 1000
                    self.logger.info(f"ACK[{label}] received in {elapsed_ms:.0f}ms")
                    return True

                self.logger.warning(f"ACK timeout from {label} target {addr}")

            except OSError as e:
                self.logger.error(f"UDP send error to {label} {addr}: {e}")
                time.sleep(0.5)

        return False

    def _send_udp_no_ack(self, packet: bytes, backup_on_fail: bool = False) -> bool:
        """Send UDP packet without waiting for ACK (batch mode).
        If backup_on_fail=True, save to backup DB on send failure."""
        if not self.udp_socket:
            if backup_on_fail:
                self.backup.save_failed_packet(packet)
            return False
        self._refresh_dns()
        sent = 0
        try:
            for label, addr in self._target_addrs():
                try:
                    with self.socket_lock:
                        self.udp_socket.sendto(packet, addr)
                    sent += 1
                except OSError as e:
                    self.logger.error(f"UDP send error to {label} {addr}: {e}")
        except Exception as e:
            self.logger.error(f"UDP send unexpected error: {e}")

        if sent > 0:
            return True

        if backup_on_fail:
            self.backup.save_failed_packet(packet)
        return False

    def _server_targets_text(self) -> str:
        targets = self._target_addrs()
        return ', '.join(f"{label}={addr[0]}:{addr[1]}" for label, addr in targets)

    def _receive_batch_acks(self, sent_packets: list, timeout: float = BATCH_ACK_TIMEOUT):
        """Receive ACKs for batch-sent packets. Unacked packets saved to backup.

        Args:
            sent_packets: list of (sequence, packet_bytes) tuples
            timeout: seconds to wait for all ACKs
        """
        if not sent_packets:
            return

        # _batch_receiving already set True before H01 sends
        pending = {seq: pkt for seq, pkt in sent_packets}
        deadline = time.time() + timeout
        self.logger.info(f"Batch ACK wait: {len(pending)} packets, timeout={timeout}s")

        # Drain any H02 ACKs caught by _receive_loop during batch send
        with self._batch_queue_lock:
            queued = self._batch_ack_queue[:]
            self._batch_ack_queue.clear()
        for qdata in queued:
            if len(qdata) >= 4:
                parsed = self.protocol.parse_h02(qdata)
                if parsed:
                    seq_q = parsed['sequence']
                    if seq_q in pending:
                        del pending[seq_q]
                        self.logger.info(f"H02 ACK received (seq={seq_q}, queued), {len(pending)} remaining")

        while pending and time.time() < deadline:
            try:
                if not self.udp_socket:
                    break
                data, addr = self.udp_socket.recvfrom(4096)
                if not data:
                    continue
                version = data[0]
                if version == VERSION_H02 and len(data) >= 4:
                    parsed = self.protocol.parse_h02(data)
                    if parsed:
                        seq = parsed['sequence']
                        if seq in pending:
                            del pending[seq]
                            self.logger.info(f"H02 ACK received (seq={seq}), {len(pending)} remaining")
                        if not self.server_reachable:
                            self.server_reachable = True
                            self.logger.info(f"Connected to {self._server_targets_text()}")
                elif version == VERSION_H03:
                    self._handle_h03(data)
                elif version == VERSION_H06 and len(data) >= 4:
                    parsed = self.protocol.parse_h06(data)
                    if parsed:
                        self._ack_event.set()
            except socket.timeout:
                continue
            except OSError as e:
                self.logger.warning(f"Batch ACK recv error: {e}")
                break
            except Exception as e:
                self.logger.error(f"Batch ACK unexpected error: {e}")
                continue

        # Save unacked packets to backup
        if pending:
            if self.server_reachable:
                self.logger.error(f"NETWORK_DOWN: {self._server_targets_text()} unreachable")
            self.server_reachable = False
            for seq, pkt in pending.items():
                self.backup.save_failed_packet(pkt)
            self.logger.warning(
                f"Batch ACK: {len(sent_packets)-len(pending)} OK, "
                f"{len(pending)} failed -> backup")
        else:
            self.logger.info(f"Batch ACK: {len(sent_packets)}/{len(sent_packets)} OK")
            if not self.server_reachable:
                self.server_reachable = True
                self.logger.info(f"Connected to {self._server_targets_text()}")
                # Communication restored → send event + 60s wait + recovery mode
                try:
                    pkt, seq = self.protocol.create_h05_event("Communication Restored")
                    self._send_udp_no_ack(pkt)
                    self.logger.info(f"H05 Communication Restored sent (seq={seq})")
                except Exception:
                    pass
                wait_s = self._recovery_wait_seconds()
                self.recovery_wait_until = time.time() + wait_s
                self.logger.info(
                    f"Recovery wait {wait_s}s before resuming H01")
                if self.backup.has_h01_backups():
                    self.recovery_mode = True
                    self.logger.info("Recovery mode activated")

        with self._batch_queue_lock:
            self._batch_ack_queue.clear()
        self._batch_receiving = False

    def _handle_received(self, data: bytes):
        """Handle incoming UDP packet (H02 ACK / H03 control / H06 HB ACK)"""
        if len(data) < 1:
            return

        version = data[0]

        if version == VERSION_H02:
            parsed = self.protocol.parse_h02(data)
            if parsed:
                result = "OK" if parsed['response'] == 0 else f"ERR({parsed['response']})"
                self.logger.debug(f"H02 ACK seq={parsed['sequence']} {result}")
                self._ack_event.set()

        elif version == VERSION_H03:
            self._handle_h03(data)

        elif version == VERSION_H06:
            parsed = self.protocol.parse_h06(data)
            if parsed:
                self.logger.info(f"H06 ACK received (seq={parsed['sequence']})")
                self._ack_event.set()

        elif version == VERSION_H07:
            self._handle_h07(data)

    def _send_to_servers(self, packet: bytes, save_on_fail: bool = True):
        """Send packet to configured server target(s)."""
        # Refresh DNS periodically
        self._refresh_dns()

        if self._send_udp(packet):
            if not self.server_reachable:
                self.logger.info(f"Connected to {self._server_targets_text()}")
                self.server_reachable = True
        else:
            if self.server_reachable:
                self.logger.error(f"NETWORK_DOWN: {self._server_targets_text()} unreachable")
            self.server_reachable = False
            if save_on_fail:
                self.backup.save_failed_packet(packet)

    # =========================================================================
    # Control Command Handler (H03 → H04)
    # =========================================================================

    def _handle_h03(self, data: bytes):
        """Handle H03 control command from server.

        Flow:
        - Info requests (ctrl=2,11): H04 → H05(result) → H06 wait
        - Control commands (ctrl=14~18): H04 → write → H05(13) → H06 → H05(14) → H06
        """
        parsed = self.protocol.parse_h03(data)
        if not parsed:
            return

        ctrl_type   = parsed['control_type']
        dev_type    = parsed['device_type']
        dev_num     = parsed['device_number']
        ctrl_val    = parsed['control_value']
        seq         = parsed['sequence']

        self.logger.info(
            f"H03 Control: type={ctrl_type} dev={dev_type}/{dev_num} val={ctrl_val}")

        # Find target handler and inv config
        target_handler = None
        target_inv = None
        for inv in self.inverters:
            if inv['device_number'] == dev_num:
                target_inv = inv
                if inv.get('control'):
                    target_handler = inv.get('handler')
                break

        applied_val = ctrl_val

        # Store control value (thread-safe)
        key = (dev_type, dev_num, ctrl_type)
        with self._control_lock:
            self.control_values[key] = applied_val
            if ctrl_type == CTRL_INV_CONTROL_INIT:
                # Reset active power limit to 100%
                self.control_values[(dev_type, dev_num, CTRL_INV_ACTIVE_POWER)] = 1000

        # ── H04 immediate response ────────────────────────────────────────
        response = RESPONSE_SUCCESS
        h04 = self.protocol.create_h04(
            seq, ctrl_type, dev_type, dev_num, applied_val, response)
        try:
            with self.socket_lock:
                if self.udp_socket:
                    self.udp_socket.sendto(h04, self.server_addr)
        except OSError as e:
            self.logger.error(f"H04 send error: {e}")
        self.logger.info(f"H04 response sent: seq={seq} -> {self.server_addr}")

        # ── Apply control and send H05 responses ────────────────────────────
        model = target_inv['model'] if target_inv else 0

        if ctrl_type == CTRL_RTU_REBOOT:
            self.logger.info("RTU Restart command received - restarting service...")
            self.logger.info("Executing: sudo systemctl restart rtu")
            import subprocess
            subprocess.Popen(['sudo', 'systemctl', 'restart', 'rtu'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return

        elif ctrl_type == CTRL_RTU_INFO:
            # H05 body type 1: RTU info (fire-and-forget)
            rtu_info = {
                'model': get_rtu_model_name(),
                'phone': str(self.rtu_id),
                'serial': str(self.rtu_id),
                'firmware': RTUClient.VERSION,
            }
            pkt, s = self.protocol.create_h05_rtu_info(rtu_info)
            self._send_udp_no_ack(pkt)
            self.logger.info(f"H05 RTU Info sent (seq={s})")

        elif ctrl_type == CTRL_INV_MODEL and target_handler:
            # H05 body type 11: Inverter model info (fire-and-forget)
            try:
                info = target_handler.read_model_info() if hasattr(target_handler, 'read_model_info') else {}
            except Exception:
                info = {}
            # Add capability flags: bit0=iv_scan, bit1=der_avm
            inv_cfg = target_inv or {}
            cap = (1 if inv_cfg.get('iv_scan') else 0) | (2 if inv_cfg.get('control') else 0)
            info['capabilities'] = cap
            pkt, s = self.protocol.create_h05_inverter_model(dev_num, model, info)
            self._send_udp_no_ack(pkt)
            self.logger.info(f"H05 INV{dev_num} Model Info sent (seq={s})")

        elif ctrl_type == CTRL_INV_IV_SCAN and target_handler:
            # IV Scan: run in separate thread (takes 8+ seconds)
            inv_cfg = target_inv or {}
            if inv_cfg.get('iv_scan'):
                t = threading.Thread(
                    target=self._execute_iv_scan,
                    args=(target_handler, dev_num, model, inv_cfg),
                    daemon=True)
                t.start()
            else:
                self.logger.warning(f"INV{dev_num}: IV Scan not supported")
                # Send H05 event to notify server
                pkt, s = self.protocol.create_h05_event(
                    f"IV Scan not supported for INV{dev_num}")
                self._send_udp_no_ack(pkt)
                self.logger.info(f"H05 IV Scan unsupported INV{dev_num} (seq={s})")

        elif ctrl_type == CTRL_INV_CONTROL_CHECK:
            # Control status check only (no write): H05(13) + H05(14) fire-and-forget
            self._send_h05_control_sequence(target_handler, dev_num, model)

        elif ctrl_type in (CTRL_INV_CONTROL_INIT, CTRL_INV_ON_OFF,
                           CTRL_INV_ACTIVE_POWER, CTRL_INV_POWER_FACTOR,
                           CTRL_INV_REACTIVE_POWER):
            # Apply to Modbus device
            if target_handler and hasattr(target_handler, 'write_control'):
                try:
                    with self._modbus_lock:
                        target_handler.write_control(ctrl_type, applied_val)
                except Exception as e:
                    self.logger.error(f"H03 control apply error: {e}")

            # H05(13) + H05(14) fire-and-forget
            self._send_h05_control_sequence(target_handler, dev_num, model)

    # =========================================================================
    # H05 Control Sequence (fire-and-forget)
    # =========================================================================

    def _send_h05_control_sequence(self, handler, dev_num: int, model: int):
        """Send H05(13) + H05(14) fire-and-forget"""
        # H05 body type 13: Control check (read back control values)
        if handler and hasattr(handler, 'read_control_status'):
            try:
                with self._modbus_lock:
                    ctrl_data = handler.read_control_status()
                if ctrl_data:
                    pkt, s = self.protocol.create_h05_control_check(
                        dev_num, model, ctrl_data)
                    self._send_udp_no_ack(pkt)
                    self.logger.info(f"H05(13) INV{dev_num} Control Check sent (seq={s})")
            except Exception as e:
                self.logger.error(f"H05(13) error: {e}")

        time.sleep(0.1)

        # H05 body type 14: Control result (monitor data)
        if handler and hasattr(handler, 'read_monitor_data'):
            try:
                with self._modbus_lock:
                    mon_data = handler.read_monitor_data()
                if mon_data:
                    pkt, s = self.protocol.create_h05_control_result(
                        dev_num, model, mon_data)
                    self._send_udp_no_ack(pkt)
                    self.logger.info(f"H05(14) INV{dev_num} Control Result sent (seq={s})")
            except Exception as e:
                self.logger.error(f"H05(14) error: {e}")

    # =========================================================================
    # IV Scan Execution (runs in separate thread)
    # =========================================================================

    def _execute_iv_scan(self, handler, dev_num: int, model: int, inv_cfg: dict):
        """Execute IV scan: poll status → send results via H05-H06 sequence.

        Runs in separate thread to avoid blocking _receive_loop.
        """
        from common.solarize_registers import IVScanStatus

        string_count = inv_cfg.get('string_count', 8)
        self.logger.info(f"IV Scan started for INV{dev_num} ({string_count} strings)")

        # Step 1: Write 0x600D = 1 (Start)
        try:
            with self._modbus_lock:
                handler.write_control(CTRL_INV_IV_SCAN, 1)
        except Exception as e:
            self.logger.error(f"IV Scan start write error: {e}")
            return

        # Step 2: Poll 0x600D every 1 second until FINISHED (max 30s)
        scan_start = time.time()
        poll_timeout = 30
        finished = False
        while time.time() - scan_start < poll_timeout:
            time.sleep(1.0)
            try:
                with self._modbus_lock:
                    status = handler.read_control_status()
                iv_status = status.get('iv_scan_status', 0) if status else 0
                if iv_status == IVScanStatus.FINISHED:
                    finished = True
                    break
                elif iv_status == IVScanStatus.RUNNING:
                    self.logger.debug(f"IV Scan polling: RUNNING ({int(time.time()-scan_start)}s)")
                else:
                    self.logger.debug(f"IV Scan polling: status={iv_status}")
            except Exception as e:
                self.logger.error(f"IV Scan poll error: {e}")

        scan_time = time.time() - scan_start

        if not finished:
            self.logger.warning(f"IV Scan timeout ({poll_timeout}s) for INV{dev_num} — inverter may be offline or night mode")
            # Notify server of IV Scan failure
            try:
                pkt, s = self.protocol.create_h05_event(
                    f"IV Scan timeout for INV{dev_num} (inverter offline or night)")
                self._send_udp_no_ack(pkt)
            except Exception:
                pass
            return

        self.logger.info(f"IV Scan completed for INV{dev_num} in {scan_time:.1f}s")

        # Step 3: Wait for H01 batch to finish (avoid socket contention)
        for _ in range(60):
            if not self._batch_receiving:
                break
            time.sleep(0.5)

        # Step 4: Send H05(12) IV Scan Success (fire-and-forget)
        pkt, s = self.protocol.create_h05_iv_scan_success(dev_num, model)
        self._send_udp_no_ack(pkt)
        self.logger.info(f"H05(12) IV Scan Success INV{dev_num} (seq={s})")
        time.sleep(0.1)

        # Step 5: Send IV data per string (fire-and-forget, 0.1s interval)
        for string_num in range(1, string_count + 1):
            try:
                with self._modbus_lock:
                    data_points = handler.get_iv_scan_data(string_num)
                if not data_points:
                    self.logger.warning(f"IV Scan: no data for string {string_num}")
                    continue

                pkt, s = self.protocol.create_h05_iv_scan_data(
                    dev_num, model, string_num, string_count, data_points)
                self._send_udp_no_ack(pkt)
                self.logger.info(
                    f"H05(15) IV String{string_num}/{string_count} INV{dev_num} (seq={s})")
                time.sleep(0.1)

            except Exception as e:
                self.logger.error(f"IV Scan string {string_num} error: {e}")

        # Step 6: Reset 0x600D = 0 (IDLE)
        try:
            with self._modbus_lock:
                handler.write_control(CTRL_INV_IV_SCAN, 0)
        except Exception:
            pass

        self.logger.info(f"IV Scan complete: INV{dev_num}, {string_count} strings sent")

    # =========================================================================
    # Firmware Update (H07 → H08)
    # =========================================================================

    def _handle_h07(self, data: bytes):
        """Handle H07 Firmware Update Request from server."""
        parsed = self.protocol.parse_h07(data)
        if not parsed:
            self.logger.error("H07 parse error")
            return

        seq = parsed['sequence']
        self.logger.info(
            f"H07 Firmware Update: {parsed['ftp_host']}:{parsed['ftp_port']}"
            f" file={parsed['ftp_path']}/{parsed['ftp_filename']}")

        if self._updating:
            self.logger.warning("Firmware update already in progress")
            h08 = self.protocol.create_h08(seq, UPDATE_RESP_BUSY)
            self._send_udp_no_ack(h08)
            return

        # H08 SUCCESS response (starting update)
        h08 = self.protocol.create_h08(seq, UPDATE_RESP_SUCCESS)
        self._send_udp_no_ack(h08)
        self.logger.info(f"H08 SUCCESS sent (seq={seq})")

        # Run update in separate thread
        t = threading.Thread(
            target=self._execute_firmware_update,
            args=(parsed, seq),
            daemon=True)
        t.start()

    def _execute_firmware_update(self, ftp_info: dict, seq: int):
        """Download firmware via FTP, backup, apply, restart.

        Uses only Python built-in modules: ftplib, tarfile, shutil.
        """
        import ftplib
        import tarfile
        import shutil
        from datetime import datetime

        self._updating = True
        download_dir = DEFAULT_DOWNLOAD_DIR
        backup_dir = DEFAULT_BACKUP_DIR
        program_dir = DEFAULT_PROGRAM_DIR
        home_dir = os.path.dirname(program_dir)

        try:
            # Ensure directories exist
            os.makedirs(download_dir, exist_ok=True)
            os.makedirs(backup_dir, exist_ok=True)

            filename = ftp_info['ftp_filename']
            local_path = os.path.join(download_dir, filename)

            # ── Step 1: FTP Download ────────────────────────────────────
            self.logger.info(f"FTP connecting to {ftp_info['ftp_host']}:{ftp_info['ftp_port']}...")
            try:
                ftp = ftplib.FTP()
                ftp.connect(ftp_info['ftp_host'], ftp_info['ftp_port'],
                            timeout=DEFAULT_FTP_TIMEOUT)
            except Exception as e:
                self.logger.error(f"FTP connect failed: {e}")
                self._send_udp_no_ack(
                    self.protocol.create_h08(seq, UPDATE_RESP_FTP_CONNECT_FAIL))
                return

            try:
                ftp.login(ftp_info['ftp_user'], ftp_info['ftp_password'])
            except Exception as e:
                self.logger.error(f"FTP login failed: {e}")
                ftp.quit()
                self._send_udp_no_ack(
                    self.protocol.create_h08(seq, UPDATE_RESP_FTP_LOGIN_FAIL))
                return

            try:
                ftp_path = ftp_info['ftp_path']
                if ftp_path and ftp_path != '/':
                    ftp.cwd(ftp_path)
                with open(local_path, 'wb') as f:
                    ftp.retrbinary(f'RETR {filename}', f.write)
                ftp.quit()
                fsize = os.path.getsize(local_path)
                self.logger.info(f"FTP download complete: {filename} ({fsize} bytes)")
            except Exception as e:
                self.logger.error(f"FTP download failed: {e}")
                try:
                    ftp.quit()
                except Exception:
                    pass
                self._send_udp_no_ack(
                    self.protocol.create_h08(seq, UPDATE_RESP_FTP_DOWNLOAD_FAIL))
                return

            # ── Step 2: Extract tar.gz ──────────────────────────────────
            extract_dir = os.path.join(download_dir, 'extract')
            if os.path.exists(extract_dir):
                shutil.rmtree(extract_dir)

            try:
                with tarfile.open(local_path, 'r:gz') as tar:
                    tar.extractall(path=extract_dir, filter='data')
                self.logger.info(f"Extracted to {extract_dir}")
            except Exception as e:
                self.logger.error(f"Extract failed: {e}")
                self._send_udp_no_ack(
                    self.protocol.create_h08(seq, UPDATE_RESP_EXTRACT_FAIL))
                return

            # ── Step 3: Backup current program ──────────────────────────
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            try:
                def _ignore_special(dir, files):
                    """Ignore non-regular files (named pipes, sockets, etc.)"""
                    ignored = []
                    for f in files:
                        path = os.path.join(dir, f)
                        if os.path.exists(path) and not os.path.isfile(path) and not os.path.isdir(path):
                            ignored.append(f)
                    return ignored

                for folder in ['rtu_program', 'common', 'config']:
                    src = os.path.join(home_dir, folder)
                    if os.path.exists(src):
                        dst = os.path.join(backup_dir, f'{folder}_{timestamp}')
                        try:
                            shutil.copytree(src, dst, ignore=_ignore_special)
                        except shutil.Error as copy_err:
                            # copytree raises Error for special files even with ignore
                            # Log warning but continue — backup is best-effort
                            self.logger.warning(f"Backup partial ({folder}): {copy_err}")
                self.logger.info(f"Backup created: {backup_dir}/*_{timestamp}")
            except Exception as e:
                self.logger.error(f"Backup failed: {e}")
                self._send_udp_no_ack(
                    self.protocol.create_h08(seq, UPDATE_RESP_APPLY_FAIL))
                return

            # ── Step 4: Apply update ────────────────────────────────────
            try:
                for folder in ['rtu_program', 'common', 'config']:
                    src = os.path.join(extract_dir, folder)
                    if os.path.exists(src):
                        dst = os.path.join(home_dir, folder)
                        # Replace entirely so uploaded config is applied exactly.
                        if os.path.exists(dst):
                            shutil.rmtree(dst)
                        shutil.copytree(src, dst)
                        self.logger.info(f"Updated: {folder}/")
                self.logger.info("Firmware update applied successfully")
            except Exception as e:
                self.logger.error(f"Apply failed: {e}")
                self._send_udp_no_ack(
                    self.protocol.create_h08(seq, UPDATE_RESP_APPLY_FAIL))
                return

            # ── Step 5: Cleanup ─────────────────────────────────────────
            try:
                shutil.rmtree(extract_dir)
                os.remove(local_path)
            except Exception:
                pass

            # ── Step 6: H08 COMPLETE ────────────────────────────────────
            self._send_udp_no_ack(
                self.protocol.create_h08(seq, UPDATE_RESP_COMPLETE))
            self.logger.info("H08 COMPLETE sent - restarting service in 2s...")
            time.sleep(2)

            # ── Step 7: Restart ─────────────────────────────────────────
            self.logger.info("Executing: sudo systemctl restart rtu")
            import subprocess
            subprocess.Popen(['sudo', 'systemctl', 'restart', 'rtu'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        except Exception as e:
            self.logger.error(f"Firmware update error: {e}")
            self._send_udp_no_ack(
                self.protocol.create_h08(seq, UPDATE_RESP_ERROR))
        finally:
            self._updating = False

    # =========================================================================
    # Device Management
    # =========================================================================

    def add_inverter(self, device_number: int, slave_id: int = 1,
                     model: int = INV_MODEL_SOLARIZE, protocol: str = 'modbus',
                     channel: int = 1, baudrate: int = 9600,
                     mppt_count: int = 0, string_count: int = 0,
                     iv_scan: bool = False, iv_scan_data_points: int = 20,
                     control: bool = False, zee_control: bool = False,
                     simulation: bool = False,
                     body_type: int = INV_BODY_BASIC_MPPT_STRING):
        """Add inverter device"""
        handler = self.modbus.add_device(
            device_type='inverter',
            slave_id=slave_id,
            protocol=protocol,
            channel=channel,
            baudrate=baudrate,
            simulation=simulation or self.simulation_mode,
            device_number=device_number,
            string_count=string_count,
            iv_scan_data_points=iv_scan_data_points
        )
        self.inverters.append({
            'device_number': device_number,
            'slave_id':      slave_id,
            'model':         model,
            'protocol':      protocol,
            'handler':       handler,
            'mppt_count':    mppt_count,
            'string_count':  string_count,
            'iv_scan':       iv_scan,
            'control':       control,
            'zee_control':   zee_control,
            'simulation':    simulation,
            'body_type':     body_type,
        })
        self.logger.info(
            f"Added INV{device_number}: SlaveID={slave_id}, Model={model}, BodyType={body_type}")

    def add_relay(self, device_number: int, slave_id: int = 1,
                  model: int = RELAY_MODEL_KDU300, protocol: str = 'modbus',
                  channel: int = 1, baudrate: int = 9600,
                  simulation: bool = False):
        """Add relay device"""
        handler = self.modbus.add_device(
            device_type='relay',
            slave_id=slave_id,
            protocol=protocol,
            channel=channel,
            baudrate=baudrate,
            simulation=simulation or self.simulation_mode,
            device_number=device_number
        )
        self.relays.append({
            'device_number': device_number,
            'slave_id':      slave_id,
            'model':         model,
            'handler':       handler,
            'simulation':    simulation,
        })
        self.logger.info(f"Added RELAY{device_number}: SlaveID={slave_id}, Model={model}")

    def add_weather(self, device_number: int, slave_id: int = 1,
                    model: int = WEATHER_MODEL_SEM5046, channel: int = 1,
                    baudrate: int = 9600, simulation: bool = False):
        """Add weather sensor device (SEM5046)"""
        handler = self.modbus.add_weather(
            device_number=device_number,
            slave_id=slave_id,
            channel=channel,
            baudrate=baudrate,
            simulation=simulation or self.simulation_mode
        )
        self.weather_sensors.append({
            'device_number': device_number,
            'slave_id':      slave_id,
            'model':         model,
            'handler':       handler,
            'simulation':    simulation,
        })
        self.logger.info(f"Added WEATHER{device_number}: SlaveID={slave_id}")

    # =========================================================================
    # Startup / Shutdown
    # =========================================================================

    def start(self) -> bool:
        """Start RTU UDP client"""
        self.logger.info(f"RTU UDP Client V{self.VERSION} starting...")
        self.logger.info(f"  RTU ID : {self.rtu_id}")
        self.logger.info(f"  Server Mode : {self.server_mode}")
        self.logger.info(f"  Server      : {self._server_targets_text()}")
        self.logger.info(f"  Period : {self.comm_period}s")

        if not self._create_udp_socket():
            return False

        self.running = True

        # Send first connection event (fire-and-forget, no backup)
        pkt, seq = self.protocol.create_h05_event(EVENT_FIRST_CONNECTION)
        self._send_udp_no_ack(pkt)
        self.logger.info(f"H05 First Connection sent (seq={seq})")

        # Initial wait before first H01 transmission; cap by configured communication period.
        wait_s = self._recovery_wait_seconds()
        self.recovery_wait_until = time.time() + wait_s
        self.logger.info(f"Waiting {wait_s}s before first H01 transmission...")

        # Start threads (send loop will check recovery_wait_until)
        threading.Thread(target=self._receive_loop,  daemon=True, name='UDP-RX').start()
        threading.Thread(target=self._modbus_poll_loop, daemon=True, name='Modbus-Poll').start()
        threading.Thread(target=self._send_loop,     daemon=True, name='UDP-TX').start()
        threading.Thread(target=self._heartbeat_loop, daemon=True, name='Heartbeat').start()
        threading.Thread(target=self._backup_loop,   daemon=True, name='Backup').start()

        # DER-AVM
        if self.der_avm_enabled and self.der_avm:
            if self.der_avm.start():
                threading.Thread(
                    target=self._der_avm_monitor_loop, daemon=True, name='DER-AVM').start()
                self.logger.info("DER-AVM Slave started on CH2")
            else:
                self.logger.error("DER-AVM Slave failed to start")

        # Activate recovery mode if backups exist
        if self.backup.has_h01_backups():
            self.recovery_mode = True
            self.logger.info("Recovery mode activated (pending backups)")

        self.logger.info("RTU started")
        return True

    def stop(self):
        """Stop RTU UDP client"""
        self.running = False
        if self.der_avm:
            self.der_avm.stop()
        with self.socket_lock:
            if self.udp_socket:
                try:
                    self.udp_socket.close()
                except OSError:
                    pass
                self.udp_socket = None
        self.modbus.disconnect_all()
        self.backup.close()
        self.logger.info("RTU stopped")

    # =========================================================================
    # Background Receive Loop (for incoming H03 commands)
    # =========================================================================

    def _receive_loop(self):
        """Background loop: receive unsolicited messages (H03 control)"""
        while self.running:
            try:
                # Yield to batch ACK receiver
                if self._batch_receiving:
                    self._recv_loop_yielded.set()
                    time.sleep(0.1)
                    continue

                self._recv_loop_yielded.clear()

                if not self.udp_socket:
                    self._recv_loop_yielded.set()
                    time.sleep(1)
                    continue

                try:
                    data, addr = self.udp_socket.recvfrom(4096)
                    if data:
                        # If batch started while we were blocked on recvfrom,
                        # queue H02 ACKs instead of consuming them
                        if self._batch_receiving and len(data) >= 1 and data[0] == VERSION_H02:
                            with self._batch_queue_lock:
                                self._batch_ack_queue.append(data)
                        else:
                            self._handle_received(data)
                except socket.timeout:
                    pass
                except OSError:
                    if self.running:
                        self.logger.warning("UDP receive error, recreating socket")
                        time.sleep(1)
                        self._create_udp_socket()

            except Exception as e:
                self.logger.error(f"Receive loop error: {e}")
                time.sleep(1)

    # =========================================================================
    # Periodic Data Send Loop (H01)
    # =========================================================================

    # ── Modbus Polling (10s interval) ────────────────────────────────────

    def _modbus_poll_loop(self):
        """Poll all Modbus devices every MODBUS_POLL_INTERVAL seconds.
        Store last successful data in _last_device_data."""
        while self.running:
            try:
                self._poll_all_devices()
            except Exception as e:
                self.logger.error(f"Modbus poll error: {e}")
            time.sleep(MODBUS_POLL_INTERVAL)

    def _poll_device_safe(self, handler, timeout=5.0):
        """Read device data with timeout to prevent Modbus hang."""
        result = [None]
        def _read():
            try:
                result[0] = handler.read_data()
            except Exception:
                pass
        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            self.logger.warning(f"Modbus read timeout ({timeout}s) for slave {getattr(handler, 'slave_id', '?')}")
            return None
        return result[0]

    def _poll_all_devices(self):
        """Read all RS485 devices and update _last_device_data on success."""
        # Inverters
        for inv in self.inverters:
            dn = inv['device_number']
            handler = inv.get('handler')
            if not handler:
                continue
            try:
                with self._modbus_lock:
                    data = self._poll_device_safe(handler)
                slave_id = getattr(handler, 'slave_id', '?')
                if data:
                    with self._poll_lock:
                        self._last_device_data[('inv', dn)] = data
                    self.logger.debug(f"[POLL] INV{dn}(slave={slave_id}): OK")
                else:
                    with self._poll_lock:
                        self._last_device_data.pop(('inv', dn), None)
                    self.logger.debug(f"[POLL] INV{dn}(slave={slave_id}): no data")
            except Exception as e:
                with self._poll_lock:
                    self._last_device_data.pop(('inv', dn), None)
                self.logger.debug(f"[POLL] INV{dn} error: {e}")
            time.sleep(DEVICE_SEND_INTERVAL)

        # Relays
        for relay in self.relays:
            dn = relay['device_number']
            handler = relay.get('handler')
            if not handler:
                continue
            try:
                with self._modbus_lock:
                    data = handler.read_data()
                slave_id = getattr(handler, 'slave_id', '?')
                if data:
                    with self._poll_lock:
                        self._last_device_data[('relay', dn)] = data
                    self.logger.debug(f"[POLL] RELAY{dn}(slave={slave_id}): OK")
                else:
                    with self._poll_lock:
                        self._last_device_data.pop(('relay', dn), None)
                    self.logger.debug(f"[POLL] RELAY{dn}(slave={slave_id}): no data")
            except Exception as e:
                with self._poll_lock:
                    self._last_device_data.pop(('relay', dn), None)
                self.logger.debug(f"[POLL] RELAY{dn} error: {e}")
            time.sleep(DEVICE_SEND_INTERVAL)

        # Weather Sensors
        for ws in self.weather_sensors:
            dn = ws['device_number']
            handler = ws.get('handler')
            if not handler:
                continue
            try:
                with self._modbus_lock:
                    data = handler.read_weather_data()
                slave_id = getattr(handler, 'slave_id', '?')
                if data:
                    with self._poll_lock:
                        self._last_device_data[('weather', dn)] = data
                    self.logger.debug(f"[POLL] WEATHER{dn}(slave={slave_id}): OK")
                else:
                    with self._poll_lock:
                        self._last_device_data.pop(('weather', dn), None)
                    self.logger.debug(f"[POLL] WEATHER{dn}(slave={slave_id}): no data")
            except Exception as e:
                with self._poll_lock:
                    self._last_device_data.pop(('weather', dn), None)
                self.logger.debug(f"[POLL] WEATHER{dn} error: {e}")
            time.sleep(DEVICE_SEND_INTERVAL)

    # ── H01 Send Loop (60s interval) ─────────────────────────────────────

    def _send_loop(self):
        """Main loop: send H01 data every comm_period seconds"""
        while self.running:
            try:
                self._send_periodic()
            except Exception as e:
                self.logger.error(f"Send loop error: {e}")
            time.sleep(self.comm_period)

    def _send_periodic(self):
        """Send H01 packets using last polled Modbus data (batch transfer)"""
        self._refresh_dns()

        # Recovery wait check (60s after first connection / communication restored)
        if time.time() < self.recovery_wait_until:
            remaining = int(self.recovery_wait_until - time.time())
            self.logger.debug(f"Recovery wait: {remaining}s remaining")
            return

        sent_packets = []  # [(seq, packet_bytes), ...]
        self._batch_receiving = True  # Block _receive_loop before sending
        try:
            self._send_periodic_inner(sent_packets)
        except Exception as e:
            self.logger.error(f"Send periodic error: {e}")
        finally:
            # Ensure _batch_receiving is always cleared
            if sent_packets:
                self._receive_batch_acks(sent_packets, BATCH_ACK_TIMEOUT)
            else:
                with self._batch_queue_lock:
                    self._batch_ack_queue.clear()
                self._batch_receiving = False

    def _send_periodic_inner(self, sent_packets: list):
        """Inner send logic — separated so finally always clears batch state"""
        # Wait for _receive_loop to exit recvfrom() and yield (max 0.6s > socket timeout 0.5s)
        self._recv_loop_yielded.wait(timeout=1.0)

        # ── Phase 1: Send H01 using last polled data ──────────────────────
        # Inverters
        for inv in self.inverters:
            device_number = inv['device_number']
            with self._poll_lock:
                data = self._last_device_data.get(('inv', device_number))
            if data:
                self.logger.info(f"[RS485] INV{device_number}: OK (polled)")
                packet, seq = self.protocol.create_h01_inverter(
                    device_number, inv['model'], data,
                    body_type=inv.get('body_type', INV_BODY_BASIC_MPPT_STRING))
                self.logger.info(
                    f"H01 TX INV{device_number} (seq={seq}) P={data.get('ac_power', 0)}W")
                if self._send_udp_no_ack(packet):
                    sent_packets.append((seq, packet))
            else:
                self.logger.warning(f"[RS485] INV{device_number}: no polled data")
                from datetime import datetime as _dt
                _hour = _dt.now().hour
                _is_nighttime = _hour >= 20 or _hour < 5
                if inv.get('protocol') == 'kstar' and _is_nighttime:
                    self.logger.info(f"[RS485] INV{device_number}: Kstar nighttime standby")
                    packet, seq = self.protocol.create_h01_nighttime_standby(
                        DEVICE_INVERTER, device_number, inv['model'])
                else:
                    packet, seq = self.protocol.create_h01_comm_fail(
                        DEVICE_INVERTER, device_number, inv['model'], INV_BODY_FAIL)
                if self._send_udp_no_ack(packet):
                    sent_packets.append((seq, packet))
            time.sleep(DEVICE_SEND_INTERVAL)

        # Relays
        for relay in self.relays:
            device_number = relay['device_number']
            with self._poll_lock:
                data = self._last_device_data.get(('relay', device_number))
            if data:
                self.logger.info(f"[RS485] RELAY{device_number}: OK (polled)")
                packet, seq = self.protocol.create_h01_relay(
                    device_number, relay['model'], data)
                self.logger.info(
                    f"H01 TX RELAY{device_number} (seq={seq}) P={data.get('total_active_power', 0):.1f}W")
                if self._send_udp_no_ack(packet):
                    sent_packets.append((seq, packet))
            else:
                self.logger.warning(f"[RS485] RELAY{device_number}: no polled data")
                packet, seq = self.protocol.create_h01_comm_fail(
                    DEVICE_PROTECTION_RELAY, device_number, relay['model'], INV_BODY_FAIL)
                if self._send_udp_no_ack(packet):
                    sent_packets.append((seq, packet))
            time.sleep(DEVICE_SEND_INTERVAL)

        # Weather Sensors
        for ws in self.weather_sensors:
            device_number = ws['device_number']
            with self._poll_lock:
                data = self._last_device_data.get(('weather', device_number))
            if data:
                self.logger.info(f"[RS485] WEATHER{device_number}: OK (polled)")
                packet, seq = self.protocol.create_h01_weather(
                    device_number, ws['model'], data)
                self.logger.info(
                    f"H01 TX WEATHER{device_number} (seq={seq}) Rad={data.get('horizontal_radiation', 0)}W/m2")
                if self._send_udp_no_ack(packet):
                    sent_packets.append((seq, packet))
            else:
                self.logger.warning(f"[RS485] WEATHER{device_number}: no polled data")
                packet, seq = self.protocol.create_h01_comm_fail(
                    DEVICE_WEATHER_STATION, device_number, ws['model'], INV_BODY_FAIL)
                if self._send_udp_no_ack(packet):
                    sent_packets.append((seq, packet))
            time.sleep(DEVICE_SEND_INTERVAL)

        # ── Phase 2: Backup packets (recovery mode only) ────────────────────
        if self.recovery_mode:
            for dev_type, dev_num in self._get_all_device_keys():
                record = self.backup.get_h01_backup_by_device(dev_type, dev_num)
                if record:
                    # Patch backup_flag=1 in header (byte offset 18)
                    patched = bytearray(record['packet'])
                    patched[18] = 1
                    patched_pkt = bytes(patched)
                    if self._send_udp_no_ack(patched_pkt):
                        sent_packets.append((record.get('sequence', 0), patched_pkt))
                        self.backup.mark_sent(record['id'])
                        self.logger.info(
                            f"[BACKUP] Sent {dev_type}/{dev_num} record {record['id']}")
                    time.sleep(DEVICE_SEND_INTERVAL)

        # ── Phase 3: Check recovery mode exit ───────────────────────────────
        if self.recovery_mode:
            if not self.backup.has_h01_backups():
                self.recovery_mode = False
                self.logger.info("Backup recovery complete - normal mode")

    def _get_all_device_keys(self):
        """Get all (device_type, device_number) pairs for backup retrieval"""
        keys = []
        for inv in self.inverters:
            keys.append((DEVICE_INVERTER, inv['device_number']))
        for relay in self.relays:
            keys.append((DEVICE_PROTECTION_RELAY, relay['device_number']))
        for ws in self.weather_sensors:
            keys.append((DEVICE_WEATHER_STATION, ws['device_number']))
        return keys

    # =========================================================================
    # Heartbeat Loop (H05 → H06)
    # =========================================================================

    def _heartbeat_loop(self):
        """Send H05 heartbeat every HEARTBEAT_INTERVAL seconds"""
        time.sleep(10)  # Initial delay
        while self.running:
            try:
                now = time.time()
                if now - self.last_heartbeat_time >= HEARTBEAT_INTERVAL:
                    pkt, seq = self.protocol.create_h05_heartbeat()
                    self.logger.debug(f"H05 HB TX (seq={seq})")
                    self._send_to_servers(pkt, save_on_fail=False)
                    self.last_heartbeat_time = now
            except Exception as e:
                self.logger.error(f"Heartbeat error: {e}")
            time.sleep(5)

    # =========================================================================
    # Backup Send Loop
    # =========================================================================

    def _backup_loop(self):
        """Periodically flush backup queue when server is reachable"""
        while self.running:
            try:
                if self.server_reachable:
                    self._flush_backup()
            except Exception as e:
                self.logger.error(f"Backup loop error: {e}")
            time.sleep(BACKUP_SEND_INTERVAL)

    def _flush_backup(self):
        """Send stored backup packets to server"""
        try:
            for _ in range(5):  # send up to 5 per flush cycle
                record = self.backup.get_h01_backup()
                if not record:
                    break
                # Patch backup_flag=1 in header (byte offset 18)
                pkt = bytearray(record['packet'])
                if len(pkt) >= HEADER_SIZE:
                    pkt[18] = 1
                pkt = bytes(pkt)
                if self._send_udp(pkt):
                    self.backup.mark_sent(record['id'])
                    self.logger.info(f"[BACKUP] Sent backup record {record['id']}")
                else:
                    break
        except Exception as e:
            self.logger.error(f"Backup flush error: {e}")

    def _send_device_backup(self, device_type: int, device_number: int):
        """Send one pending backup entry for specified device"""
        try:
            record = self.backup.get_h01_backup_by_device(device_type, device_number)
            if record:
                # Patch backup_flag=1 in header (byte offset 18)
                pkt = bytearray(record['packet'])
                if len(pkt) >= HEADER_SIZE:
                    pkt[18] = 1
                pkt = bytes(pkt)
                if self._send_udp(pkt):
                    self.backup.mark_sent(record['id'])
                    self.logger.info(
                        f"[BACKUP] Sent {device_type}/{device_number} record {record['id']}")
        except Exception as e:
            self.logger.debug(f"Device backup send error: {e}")

    # =========================================================================
    # DER-AVM Monitor Loop
    # =========================================================================

    def _der_avm_monitor_loop(self):
        """Monitor DER-AVM slave and send control updates"""
        while self.running:
            try:
                if self.der_avm:
                    self.der_avm_data = self.der_avm.get_all_data()
            except Exception as e:
                self.logger.error(f"DER-AVM monitor error: {e}")
            time.sleep(1)

    # =========================================================================
    # Packet Logging Helpers
    # =========================================================================

    def _log_packet_hex(self, direction: str, pkt_type: str, data: bytes):
        hex_str = data[:32].hex().upper()
        if len(data) > 32:
            hex_str += f"... ({len(data)}B)"
        self.logger.debug(f"{direction} {pkt_type}: {hex_str}")


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    import io

    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(line_buffering=True)
    else:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding=sys.stdout.encoding,
            errors=sys.stdout.errors, line_buffering=True)

    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description='RTU UDP Client V1.0.0')
    parser.add_argument('-c', '--config', default='../config',
                        help='Config directory path')
    parser.add_argument('-s', '--simulation', action='store_true',
                        help='Run in simulation mode')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='Enable debug logging')
    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO

    class FlushStreamHandler(logging.StreamHandler):
        def emit(self, record):
            super().emit(record)
            self.flush()

    handler = FlushStreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logging.root.handlers = []
    logging.root.addHandler(handler)
    logging.root.setLevel(log_level)

    logger = logging.getLogger('RTU')
    logger.info(f"RTU UDP Client V{RTUClient.VERSION}")

    # Load config
    config_dir = args.config
    if not os.path.isabs(config_dir):
        config_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), config_dir)

    config = SimpleConfig(config_dir)
    if not config.load():
        logger.error("Failed to load configuration")
        sys.exit(1)

    config.print_config()

    # Load device config from rs485_ch1.ini
    rs485_cfg_path = os.path.join(config_dir, 'rs485_ch1.ini')
    client = RTUClient(config=config, simulation_mode=args.simulation)

    if os.path.exists(rs485_cfg_path):
        dev_cfg = _configparser.ConfigParser()
        dev_cfg.read(rs485_cfg_path, encoding='utf-8')
        for section in dev_cfg.sections():
            d = dict(dev_cfg[section])

            # Skip disabled devices
            if d.get('installed', 'YES').upper() != 'YES':
                continue

            # device_type may be numeric (1/4/5) or string ('inverter'/'relay'/'weather')
            dev_type_raw = d.get('device_type', '').strip()
            try:
                dev_type_int = int(dev_type_raw)
            except ValueError:
                dev_type_int = -1
            dev_type_str = dev_type_raw.lower()

            is_inverter = (dev_type_int == DEVICE_INVERTER)        or (dev_type_str == 'inverter')
            is_relay    = (dev_type_int == DEVICE_PROTECTION_RELAY) or (dev_type_str in ('relay', 'protection_relay'))
            is_weather  = (dev_type_int == DEVICE_WEATHER_STATION)  or (dev_type_str in ('weather', 'weather_station'))

            try:
                dev_num   = int(d.get('device_number', 1))
                slave_id  = int(d.get('slave_id', 1))
                model     = int(d.get('model', 0))
                channel   = int(d.get('channel', 1))
                baudrate  = int(d.get('baudrate', 9600))
                protocol  = d.get('protocol', 'modbus').lower().strip()
                sim       = d.get('simulation', 'false').lower() == 'true'

                if is_inverter:
                    body_type = int(d.get('body_type', INV_BODY_BASIC_MPPT_STRING))
                    mppt      = int(d.get('mppt_count', 0))
                    strings   = int(d.get('string_count', 0))
                    ctrl      = d.get('control', 'NONE').upper().strip() != 'NONE'
                    iv_scan_  = d.get('iv_scan', 'false').lower() == 'true'
                    iv_pts    = int(d.get('iv_scan_data_points', 64))
                    client.add_inverter(dev_num, slave_id, model,
                                        protocol=protocol,
                                        channel=channel, baudrate=baudrate,
                                        mppt_count=mppt, string_count=strings,
                                        iv_scan=iv_scan_, iv_scan_data_points=iv_pts,
                                        control=ctrl,
                                        body_type=body_type, simulation=sim)
                elif is_relay:
                    client.add_relay(dev_num, slave_id, model,
                                     channel=channel, baudrate=baudrate,
                                     simulation=sim)
                elif is_weather:
                    client.add_weather(dev_num, slave_id, model,
                                       channel=channel, baudrate=baudrate,
                                       simulation=sim)
                else:
                    logger.debug(f"Skipping unknown device_type '{dev_type_raw}' in [{section}]")
            except Exception as e:
                logger.error(f"Device config error [{section}]: {e}")
    else:
        logger.warning(f"Device config not found: {rs485_cfg_path}")

    # Signal handlers
    def signal_handler(signum, frame):
        logger.info(f"Signal {signum} received, stopping...")
        client.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if not client.start():
        logger.error("Failed to start RTU client")
        sys.exit(1)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()


if __name__ == '__main__':
    main()
