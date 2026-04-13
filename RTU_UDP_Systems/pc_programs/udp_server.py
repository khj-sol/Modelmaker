#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RTU Production UDP Server V1.1.0

Production-grade UDP server for receiving RTU data with:
- SQLite data persistence (WAL mode)
- Duplicate packet detection
- Per-device rate limiting
- RTU connection tracking with timeout
- Rotating file + console logging
- ACK-first pattern (send ACK before parsing/storing)
- Socket lock for thread-safe sendto
- Graceful shutdown
- Data retention cleanup
- Optional interactive H03 control menu
- Built-in FTP server for firmware distribution
"""

import socket
import struct
import time
import threading
import sqlite3
import logging
import argparse
import signal
import sys
import os
from datetime import datetime, timezone, timedelta
from collections import OrderedDict
from logging.handlers import RotatingFileHandler

# FTP Server imports
try:
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
    FTP_AVAILABLE = True
except ImportError:
    FTP_AVAILABLE = False

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.protocol_constants import *

logger = logging.getLogger('Server')

KST = timezone(timedelta(hours=9))


def now_kst() -> str:
    return datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')


def epoch_to_kst(ts: int) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=KST).strftime('%Y-%m-%d %H:%M:%S')
    except (OSError, ValueError, OverflowError):
        return None


def setup_logging(log_dir: str, level: str = "INFO"):
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "udp_server.log")

    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')

    fh = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(fh)
    root.addHandler(ch)


# =============================================================================
# SyncDB — SQLite persistence layer
# =============================================================================

class SyncDB:
    BATCH_SIZE = 100
    BATCH_INTERVAL = 2.0

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._pending = 0
        self._last_commit = 0.0
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.init_db()
        # Start batch commit timer thread
        self._running = True
        threading.Thread(target=self._batch_commit_loop, daemon=True).start()

    def init_db(self):
        with self._lock:
            c = self.conn
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")

            c.execute("""CREATE TABLE IF NOT EXISTS rtu_registry (
                rtu_id INTEGER PRIMARY KEY,
                ip TEXT, port INTEGER,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                last_seen TEXT, status TEXT DEFAULT 'online')""")

            c.execute("""CREATE TABLE IF NOT EXISTS inverter_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                rtu_id INTEGER, device_number INTEGER, model INTEGER,
                pv_voltage REAL, pv_current REAL, pv_power REAL,
                r_voltage REAL, s_voltage REAL, t_voltage REAL,
                r_current REAL, s_current REAL, t_current REAL,
                ac_power REAL, power_factor REAL, frequency REAL,
                cumulative_energy REAL, status INTEGER,
                backup_flag INTEGER DEFAULT 0, original_timestamp TEXT)""")

            c.execute("""CREATE TABLE IF NOT EXISTS relay_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                rtu_id INTEGER, device_number INTEGER,
                r_voltage REAL, s_voltage REAL, t_voltage REAL,
                r_current REAL, s_current REAL, t_current REAL,
                total_power REAL, power_factor REAL, frequency REAL,
                backup_flag INTEGER DEFAULT 0, original_timestamp TEXT)""")

            c.execute("""CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                rtu_id INTEGER, event_type TEXT, body_type INTEGER, detail TEXT)""")

            c.execute("CREATE INDEX IF NOT EXISTS idx_inv_rtu_ts ON inverter_data(rtu_id, timestamp)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_relay_rtu_ts ON relay_data(rtu_id, timestamp)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_event_rtu_ts ON event_log(rtu_id, timestamp)")
            c.commit()
        logger.info(f"Database initialized: {self.db_path}")

    def close(self):
        self._running = False
        with self._lock:
            try:
                self.conn.commit()
            except Exception as e:
                logger.error(f"Final commit failed: {e}")
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as e:
                logger.warning(f"WAL checkpoint failed: {e}")
            self.conn.close()

    def _batch_commit_loop(self):
        while self._running:
            time.sleep(self.BATCH_INTERVAL)
            with self._lock:
                if self._pending > 0:
                    try:
                        self.conn.commit()
                    except Exception as e:
                        logger.error(f"Batch commit failed: {e}")
                    self._pending = 0
                    self._last_commit = time.time()

    def _maybe_commit(self):
        """Called inside _lock. Commit if batch size reached."""
        self._pending += 1
        if self._pending >= self.BATCH_SIZE:
            self.conn.commit()
            self._pending = 0
            self._last_commit = time.time()

    def upsert_rtu(self, rtu_id: int, ip: str, port: int):
        ts = now_kst()
        with self._lock:
            self.conn.execute(
                """INSERT INTO rtu_registry (rtu_id, ip, port, first_seen, last_seen, status)
                   VALUES (?, ?, ?, ?, ?, 'online')
                   ON CONFLICT(rtu_id) DO UPDATE SET ip=?, port=?, last_seen=?, status='online'""",
                (rtu_id, ip, port, ts, ts, ip, port, ts))
            self._maybe_commit()  # 배치 커밋에 포함

    def set_rtu_offline(self, rtu_id: int):
        with self._lock:
            self.conn.execute(
                "UPDATE rtu_registry SET status='offline', last_seen=? WHERE rtu_id=?",
                (now_kst(), rtu_id))
            self._maybe_commit()  # 배치 커밋에 포함

    def save_inverter(self, rtu_id: int, p: dict):
        with self._lock:
            self.conn.execute(
                """INSERT INTO inverter_data
                   (timestamp, rtu_id, device_number, model,
                    pv_voltage, pv_current, pv_power,
                    r_voltage, s_voltage, t_voltage,
                    r_current, s_current, t_current,
                    ac_power, power_factor, frequency,
                    cumulative_energy, status, backup_flag, original_timestamp)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (now_kst(), rtu_id, p['device_number'], p['model'],
                 p['pv_voltage'], p['pv_current'], p['pv_power'],
                 p['r_voltage'], p['s_voltage'], p['t_voltage'],
                 p['r_current'], p['s_current'], p['t_current'],
                 p['ac_power'], p['power_factor'], p['frequency'],
                 p['cumulative_energy'], p['status'],
                 p.get('backup', 0), p.get('original_timestamp')))
            self._maybe_commit()

    def save_relay(self, rtu_id: int, p: dict):
        with self._lock:
            self.conn.execute(
                """INSERT INTO relay_data
                   (timestamp, rtu_id, device_number,
                    r_voltage, s_voltage, t_voltage,
                    r_current, s_current, t_current,
                    total_power, power_factor, frequency,
                    backup_flag, original_timestamp)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (now_kst(), rtu_id, p['device_number'],
                 p['r_voltage'], p['s_voltage'], p['t_voltage'],
                 p['r_current'], p['s_current'], p['t_current'],
                 p['total_power'], p['power_factor'], p['frequency'],
                 p.get('backup', 0), p.get('original_timestamp')))
            self._maybe_commit()

    def save_event(self, rtu_id: int, event_type: str, body_type: int, detail: str):
        with self._lock:
            self.conn.execute(
                "INSERT INTO event_log (timestamp, rtu_id, event_type, body_type, detail) VALUES (?,?,?,?,?)",
                (now_kst(), rtu_id, event_type, body_type, detail))
            self._maybe_commit()

    def cleanup_old_data(self, retention_days: int) -> int:
        cutoff = (datetime.now(KST) - timedelta(days=retention_days)).strftime('%Y-%m-%d %H:%M:%S')
        total = 0
        for table in ('inverter_data', 'relay_data', 'event_log'):
            while True:
                with self._lock:
                    cur = self.conn.execute(
                        f"DELETE FROM {table} WHERE rowid IN "
                        f"(SELECT rowid FROM {table} WHERE timestamp < ? LIMIT 5000)",
                        (cutoff,))
                    deleted = cur.rowcount
                    self.conn.commit()
                total += deleted
                if deleted < 5000:
                    break
        # Reclaim disk space after large deletes (락 없이 실행 — WAL 모드에서 읽기 가능)
        if total > 1000:
            try:
                self.conn.execute("VACUUM")
            except Exception as e:
                logger.warning(f"VACUUM failed: {e}")
        return total

    def count_rows(self) -> dict:
        with self._lock:
            result = {}
            for table in ('rtu_registry', 'inverter_data', 'relay_data', 'event_log'):
                cur = self.conn.execute(f"SELECT COUNT(*) FROM {table}")
                result[table] = cur.fetchone()[0]
            return result


# =============================================================================
# DuplicateDetector
# =============================================================================

class DuplicateDetector:
    MAX_SIZE = 50000

    def __init__(self, ttl: float = 300.0):
        self.ttl = ttl
        self._seen: OrderedDict[tuple, float] = OrderedDict()
        self._last_cleanup = time.time()
        self._lock = threading.Lock()

    def is_duplicate(self, rtu_id: int, seq: int) -> bool:
        key = (rtu_id, seq)
        now = time.time()
        with self._lock:
            if now - self._last_cleanup > 60:
                self._cleanup(now)
            if key in self._seen:
                self._seen.move_to_end(key)
                self._seen[key] = now
                return True
            self._seen[key] = now
            # Evict oldest if over max size
            while len(self._seen) > self.MAX_SIZE:
                self._seen.popitem(last=False)
            return False

    def _cleanup(self, now: float):
        self._last_cleanup = now
        # Remove expired entries from oldest first
        while self._seen:
            key, ts = next(iter(self._seen.items()))
            if now - ts > self.ttl:
                del self._seen[key]
            else:
                break


# =============================================================================
# RateLimiter
# =============================================================================

class RateLimiter:
    MAX_SIZE = 10000

    def __init__(self, min_interval: float = 10.0):
        self.min_interval = min_interval
        self._last: OrderedDict[tuple, float] = OrderedDict()
        self._last_cleanup = 0.0
        self._lock = threading.Lock()

    def is_allowed(self, rtu_id: int, dev_type: int, dev_num: int) -> bool:
        key = (rtu_id, dev_type, dev_num)
        now = time.time()
        with self._lock:
            if now - self._last_cleanup > 60:
                self._last_cleanup = now
                while self._last:
                    k, t = next(iter(self._last.items()))
                    if now - t > 120:
                        del self._last[k]
                    else:
                        break
            last = self._last.get(key, 0)
            if now - last < self.min_interval:
                return False
            self._last[key] = now
            self._last.move_to_end(key)
            # Evict oldest if over max size
            while len(self._last) > self.MAX_SIZE:
                self._last.popitem(last=False)
            return True


# =============================================================================
# RTUConnectionTracker
# =============================================================================

class RTUConnectionTracker:
    def __init__(self, timeout: float = 120.0):
        self.timeout = timeout
        self._rtus: dict[int, dict] = {}
        self._ip_to_rtu: dict[str, int] = {}  # reverse lookup: ip -> rtu_id
        self._lock = threading.Lock()

    def update(self, rtu_id: int, ip: str, port: int) -> bool:
        with self._lock:
            is_new = rtu_id not in self._rtus
            old_info = self._rtus.get(rtu_id)
            if old_info and old_info['ip'] != ip:
                self._ip_to_rtu.pop(old_info['ip'], None)
            self._rtus[rtu_id] = {'ip': ip, 'port': port, 'last_seen': time.time()}
            self._ip_to_rtu[ip] = rtu_id
            return is_new

    def get_stale(self) -> list:
        now = time.time()
        with self._lock:
            return [rid for rid, info in self._rtus.items()
                    if now - info['last_seen'] > self.timeout]

    def get_addr(self, rtu_id: int) -> tuple | None:
        with self._lock:
            info = self._rtus.get(rtu_id)
            return (info['ip'], info['port']) if info else None

    def get_rtu_by_ip(self, ip: str) -> int | None:
        with self._lock:
            return self._ip_to_rtu.get(ip)

    def get_all(self) -> dict:
        with self._lock:
            return dict(self._rtus)

    def remove(self, rtu_id: int):
        with self._lock:
            info = self._rtus.pop(rtu_id, None)
            if info:
                self._ip_to_rtu.pop(info['ip'], None)


# =============================================================================
# FTPServerManager (from udp_test_server.py)
# =============================================================================

class FTPServerManager:
    def __init__(self, port: int = DEFAULT_BUILTIN_FTP_PORT,
                 root_dir: str = DEFAULT_FTP_ROOT_DIR):
        self.port = port
        self.root_dir = os.path.abspath(root_dir)
        self.server = None
        self.server_thread = None
        self.running = False
        self.users = {DEFAULT_FTP_USER: DEFAULT_FTP_PASSWORD}
        self.authorizer = None

    def start(self) -> bool:
        if not FTP_AVAILABLE:
            logger.error("pyftpdlib not installed")
            return False
        if self.running:
            return True
        try:
            os.makedirs(self.root_dir, exist_ok=True)
            self.authorizer = DummyAuthorizer()
            for user, pw in self.users.items():
                self.authorizer.add_user(user, pw, self.root_dir, perm="elradfmw")
            # 클래스 변수 오염을 방지하기 위해 인스턴스 전용 서브클래스 생성
            _auth = self.authorizer
            handler = type('_RTUFTPHandler', (FTPHandler,), {
                'authorizer': _auth,
                'passive_ports': range(60000, 60100),
                'banner': "RTU Production Server FTP",
            })
            self.server = FTPServer(("0.0.0.0", self.port), handler)
            self.server.max_cons = 10
            self.server.max_cons_per_ip = 5
            self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.server_thread.start()
            self.running = True
            logger.info(f"FTP server started on port {self.port}")
            return True
        except OSError as e:
            logger.error(f"FTP server start failed: {e}")
            return False

    def stop(self):
        if not self.running:
            return
        self.running = False
        if self.server:
            try:
                self.server.close_all()
            except Exception:
                pass
        logger.info("FTP server stopped")

    def is_running(self) -> bool:
        return self.running


# =============================================================================
# ProductionUDPServer
# =============================================================================

class ProductionUDPServer:
    VERSION = "1.1.0"

    def __init__(self, listen_port=DEFAULT_SERVER_PORT, rtu_port=DEFAULT_RTU_LOCAL_PORT,
                 ftp_port=DEFAULT_BUILTIN_FTP_PORT, ftp_root=DEFAULT_FTP_ROOT_DIR,
                 db_path='udp_server.db', retention_days=30,
                 rate_limit_interval=10.0, interactive=False):
        self.listen_port = listen_port
        self.rtu_port = rtu_port
        self.interactive = interactive

        self.socket = None
        self._socket_lock = threading.Lock()
        self._seq_lock = threading.Lock()
        self.running = False
        self.seq = 1000

        self.db = SyncDB(db_path)
        self.dup = DuplicateDetector(ttl=300)
        self.limiter = RateLimiter(min_interval=rate_limit_interval)
        self.tracker = RTUConnectionTracker(timeout=120)
        self.ftp = FTPServerManager(port=ftp_port, root_dir=ftp_root)
        self.retention_days = retention_days

        self._stats_lock = threading.Lock()
        self.stats = {
            'h01_received': 0, 'h02_sent': 0, 'h03_sent': 0,
            'h04_received': 0, 'h05_received': 0, 'h06_sent': 0,
            'h08_received': 0, 'duplicates': 0, 'rate_limited': 0,
            'start_time': 0,
        }

    def _inc_stat(self, key: str):
        with self._stats_lock:
            self.stats[key] += 1

    def _get_stats(self) -> dict:
        with self._stats_lock:
            return dict(self.stats)

    # ----- lifecycle -----

    def start(self):
        logger.info("=" * 60)
        logger.info(f"  RTU Production UDP Server V{self.VERSION}")
        logger.info(f"  Protocol: {PROTOCOL_VERSION}")
        logger.info(f"  Listen: 0.0.0.0:{self.listen_port}")
        logger.info(f"  DB: {self.db.db_path}")
        logger.info("=" * 60)

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(('0.0.0.0', self.listen_port))
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)  # 1MB for ~1600 RTUs
        self.socket.settimeout(0.5)
        self.running = True
        self.stats['start_time'] = time.time()

        self._recv_thread = threading.Thread(target=self._receive_loop, daemon=True, name="recv")
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True, name="cleanup")
        self._recv_thread.start()
        self._cleanup_thread.start()

        # Startup data retention cleanup
        deleted = self.db.cleanup_old_data(self.retention_days)
        if deleted > 0:
            logger.info(f"Startup cleanup: {deleted} rows removed (>{self.retention_days} days)")

        logger.info("Server started. Waiting for RTU connections...")

        if self.interactive:
            self._interactive_menu()
        else:
            try:
                while self.running:
                    time.sleep(1)
            except (KeyboardInterrupt, EOFError):
                pass
            self.stop()

    def stop(self):
        if not self.running:
            return
        self.running = False
        logger.info("Shutting down...")
        self.ftp.stop()
        if self.socket:
            try:
                self.socket.close()
            except Exception as e:
                logger.warning(f"Socket close error: {e}")
        # Wait for threads to finish
        for t in (getattr(self, '_recv_thread', None), getattr(self, '_cleanup_thread', None)):
            if t and t.is_alive():
                t.join(timeout=3)
        self.db.close()
        stats = self._get_stats()
        uptime = int(time.time() - stats['start_time'])
        logger.info(f"Stats: {stats}")
        logger.info(f"Uptime: {uptime}s. Server stopped.")

    # ----- receive loop -----

    def _receive_loop(self):
        while self.running:
            try:
                data, addr = self.socket.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                if not self.running:
                    break
                continue

            if len(data) < 1:
                continue

            version = data[0]
            try:
                if version == VERSION_H01:
                    self._handle_h01(data, addr)
                elif version == VERSION_H04:
                    self._handle_h04(data, addr)
                elif version == VERSION_H05:
                    self._handle_h05(data, addr)
                elif version == VERSION_H08:
                    self._handle_h08(data, addr)
                else:
                    logger.debug(f"Unknown version {version} from {addr}")
            except Exception as e:
                logger.error(f"Packet error v={version} from {addr}: {e}")

    # ----- cleanup loop -----

    def _cleanup_loop(self):
        last_retention_cleanup = time.time()
        while self.running:
            time.sleep(60)

            # RTU timeout check
            for rtu_id in self.tracker.get_stale():
                self.db.set_rtu_offline(rtu_id)
                self.tracker.remove(rtu_id)
                logger.warning(f"RTU {rtu_id} offline (timeout)")

            # Hourly data retention cleanup (time-based)
            if time.time() - last_retention_cleanup >= 3600:
                last_retention_cleanup = time.time()
                try:
                    deleted = self.db.cleanup_old_data(self.retention_days)
                    if deleted > 0:
                        logger.info(f"Data cleanup: {deleted} rows removed (>{self.retention_days} days)")
                except Exception as e:
                    logger.error(f"Data cleanup failed: {e}")

    # ----- H01 handler -----

    def _handle_h01(self, data: bytes, addr: tuple):
        if len(data) < HEADER_SIZE:
            return

        header = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
        seq = header[1]
        rtu_id = header[2]
        timestamp = header[3]
        dev_type = header[4]
        dev_num = header[5]
        model = header[6]
        backup = header[7]
        body_type = header[8]

        self._inc_stat('h01_received')

        # ACK first
        ack = struct.pack(H02_FORMAT, VERSION_H02, seq, RESPONSE_SUCCESS)
        with self._socket_lock:
            self.socket.sendto(ack, addr)
        self._inc_stat('h02_sent')

        # Duplicate check (after ACK)
        if self.dup.is_duplicate(rtu_id, seq):
            self._inc_stat('duplicates')
            logger.debug(f"Duplicate H01 rtu={rtu_id} seq={seq}")
            return

        # Rate limit (after ACK)
        if not self.limiter.is_allowed(rtu_id, dev_type, dev_num):
            self._inc_stat('rate_limited')
            return

        # Track RTU
        is_new = self.tracker.update(rtu_id, addr[0], addr[1])
        if is_new:
            logger.info(f"New RTU registered: {rtu_id} from {addr[0]}:{addr[1]}")
        self.db.upsert_rtu(rtu_id, addr[0], addr[1])

        dev_name = DEVICE_TYPE_NAMES.get(dev_type, f"Type{dev_type}")
        orig_ts = epoch_to_kst(timestamp)

        # Comm failure
        if body_type < 0:
            names = {-3: "ZEE_SKIP", -2: "PACKET_ERROR", -1: "COMM_FAIL"}
            detail = names.get(body_type, f"ERROR({body_type})")
            self.db.save_event(rtu_id, "comm_fail", body_type,
                               f"{dev_name}{dev_num}: {detail}")
            logger.warning(f"H01 RTU:{rtu_id} {dev_name}{dev_num} {detail}")
            return

        # Parse body
        body = data[HEADER_SIZE:]
        parsed = None

        if dev_type == DEVICE_INVERTER:
            parsed = self._parse_inverter(body, body_type, dev_num, model)
        elif dev_type == DEVICE_PROTECTION_RELAY:
            parsed = self._parse_relay(body, dev_num, model)

        if parsed:
            parsed['backup'] = backup
            parsed['original_timestamp'] = orig_ts

            if dev_type == DEVICE_INVERTER:
                self.db.save_inverter(rtu_id, parsed)
                bk = " [BACKUP]" if backup else ""
                logger.info(f"H01 RTU:{rtu_id} {dev_name}{dev_num} "
                            f"P={parsed['ac_power']}W{bk}")
            elif dev_type == DEVICE_PROTECTION_RELAY:
                self.db.save_relay(rtu_id, parsed)
                logger.info(f"H01 RTU:{rtu_id} {dev_name}{dev_num} "
                            f"P={parsed['total_power']:.0f}W")

    # ----- H05 handler -----

    def _handle_h05(self, data: bytes, addr: tuple):
        if len(data) < HEADER_SIZE:
            return

        header = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
        seq = header[1]
        rtu_id = header[2]
        body_type = header[8]
        dev_num = header[5]

        self._inc_stat('h05_received')

        # ACK first
        ack = struct.pack(H06_FORMAT, VERSION_H06, seq, RESPONSE_SUCCESS)
        with self._socket_lock:
            self.socket.sendto(ack, addr)
        self._inc_stat('h06_sent')

        # Duplicate
        if self.dup.is_duplicate(rtu_id, seq):
            self._inc_stat('duplicates')
            return

        # Track RTU
        self.tracker.update(rtu_id, addr[0], addr[1])
        self.db.upsert_rtu(rtu_id, addr[0], addr[1])

        body = data[HEADER_SIZE:]
        event_type = "unknown"
        detail = ""

        if body_type == BODY_TYPE_HEARTBEAT:
            event_type = "heartbeat"
            detail = "PING"
        elif body_type == BODY_TYPE_RTU_EVENT:
            event_type = "rtu_event"
            if len(body) > 0:
                elen = body[0]
                if len(body) > elen:
                    detail = body[1:1+elen].decode('utf-8', errors='ignore')
        elif body_type == BODY_TYPE_RTU_INFO:
            event_type = "rtu_info"
            pos = 0
            parts = []
            for name in ['Model', 'Phone', 'Serial', 'Firmware']:
                if pos < len(body):
                    sz = body[pos]; pos += 1
                    if pos + sz <= len(body):
                        val = body[pos:pos+sz].decode('utf-8', errors='ignore')
                        parts.append(f"{name}={val}")
                        pos += sz
            detail = ", ".join(parts)
        elif body_type == BODY_TYPE_POWER_OUTAGE:
            event_type = "power_outage"
            if len(body) >= 4:
                t = struct.unpack('>I', body[:4])[0]
                detail = f"Outage at {epoch_to_kst(t)}"
        elif body_type == BODY_TYPE_POWER_RESTORE:
            event_type = "power_restore"
            if len(body) >= 12:
                ot, rt, dur = struct.unpack('>III', body[:12])
                detail = f"Outage={epoch_to_kst(ot)}, Restore={epoch_to_kst(rt)}, Duration={dur}s"
        elif body_type == BODY_TYPE_INVERTER_MODEL:
            event_type = "inverter_model"
            pos = 0
            parts = []
            for name in ['Model', 'Serial']:
                if pos < len(body):
                    sz = body[pos]; pos += 1
                    if pos + sz <= len(body):
                        val = body[pos:pos+sz].decode('utf-8', errors='ignore')
                        parts.append(f"{name}={val}")
                        pos += sz
            detail = f"INV{dev_num}: " + ", ".join(parts)
        elif body_type == BODY_TYPE_CONTROL_CHECK:
            event_type = "control_check"
            if len(body) >= 10:
                rs, pf, om, rp, ap = struct.unpack('>HhHHH', body[:10])
                detail = (f"INV{dev_num}: Run={'ON' if rs==0 else 'OFF'}, "
                          f"PF={pf/1000:.3f}, RP={rp/10:.1f}%, AP={ap/10:.1f}%")
        elif body_type == BODY_TYPE_CONTROL_RESULT:
            event_type = "control_result"
            detail = f"INV{dev_num} power monitoring data"
        elif body_type == BODY_TYPE_IV_SCAN_SUCCESS:
            event_type = "iv_scan_success"
            detail = f"INV{dev_num} IV scan complete"
        elif body_type == BODY_TYPE_IV_SCAN_DATA:
            event_type = "iv_scan_data"
            if len(body) >= 3:
                ts, sn, pts = struct.unpack('>BBB', body[:3])
                detail = f"INV{dev_num} String {sn}/{ts}, {pts} points"
        else:
            event_type = f"body_type_{body_type}"
            detail = f"len={len(body)}"

        self.db.save_event(rtu_id, event_type, body_type, detail)
        if body_type != BODY_TYPE_HEARTBEAT:
            logger.info(f"H05 RTU:{rtu_id} [{event_type}] {detail}")

    # ----- H04 handler -----

    def _handle_h04(self, data: bytes, addr: tuple):
        if len(data) < H04_SIZE:
            return

        vals = struct.unpack(H04_FORMAT, data[:H04_SIZE])
        seq, ctrl_type, dev_type, dev_num, ctrl_val, resp = vals[1:7]
        self._inc_stat('h04_received')

        ctrl_name = CONTROL_TYPE_NAMES.get(ctrl_type, f"Type{ctrl_type}")
        resp_str = "SUCCESS" if resp == 0 else f"FAIL({resp})"
        logger.info(f"H04 {ctrl_name} INV{dev_num} val={ctrl_val} -> {resp_str}")

        # Find RTU by IP (O(1) lookup)
        rtu_id = self.tracker.get_rtu_by_ip(addr[0])
        if rtu_id:
            self.db.save_event(rtu_id, "h04_response", ctrl_type,
                               f"{ctrl_name}: val={ctrl_val}, resp={resp_str}")

    # ----- H08 handler -----

    def _handle_h08(self, data: bytes, addr: tuple):
        if len(data) < H08_SIZE:
            return
        vals = struct.unpack(H08_FORMAT, data[:H08_SIZE])
        resp = vals[4]
        self._inc_stat('h08_received')

        resp_names = {
            0: "SUCCESS", 1: "COMPLETE", -1: "ERROR",
            -2: "FTP_CONNECT_FAIL", -3: "FTP_LOGIN_FAIL",
            -4: "FTP_DOWNLOAD_FAIL", -5: "EXTRACT_FAIL",
            -6: "APPLY_FAIL", -7: "BUSY", -8: "HASH_FAIL",
        }
        logger.info(f"H08 Firmware: {resp_names.get(resp, f'UNKNOWN({resp})')}")

    # ----- parsers -----

    def _parse_inverter(self, body: bytes, body_type: int, dev_num: int, model: int):
        if len(body) < INV_BASIC_SIZE:
            return None

        b = struct.unpack(INV_BASIC_FORMAT, body[:INV_BASIC_SIZE])
        return {
            'device_number': dev_num, 'model': model, 'body_type': body_type,
            'pv_voltage': b[0], 'pv_current': b[1], 'pv_power': b[2],
            'r_voltage': b[3], 's_voltage': b[4], 't_voltage': b[5],
            'r_current': b[6], 's_current': b[7], 't_current': b[8],
            'ac_power': b[9], 'power_factor': b[10] / 1000.0,
            'frequency': b[11] / 10.0, 'cumulative_energy': b[12],
            'status': b[13],
        }

    def _parse_relay(self, body: bytes, dev_num: int, model: int):
        if len(body) < RELAY_BASIC_SIZE:
            return None

        v = struct.unpack(RELAY_BASIC_FORMAT, body[:RELAY_BASIC_SIZE])
        return {
            'device_number': dev_num, 'model': model,
            'r_voltage': v[0], 's_voltage': v[1], 't_voltage': v[2],
            'r_current': v[3], 's_current': v[4], 't_current': v[5],
            'total_power': v[9], 'power_factor': v[10], 'frequency': v[11],
        }

    # ----- send commands -----

    def _next_seq(self) -> int:
        with self._seq_lock:
            self.seq = (self.seq % 65535) + 1
            return self.seq

    def _send_h03(self, rtu_id: int, ctrl_type: int, dev_type: int,
                  dev_num: int, value: int) -> bool:
        addr = self.tracker.get_addr(rtu_id)
        if not addr:
            logger.error(f"RTU {rtu_id} not found")
            return False
        seq = self._next_seq()
        pkt = struct.pack(H03_FORMAT, VERSION_H03, seq, ctrl_type, dev_type, dev_num, value)
        with self._socket_lock:
            self.socket.sendto(pkt, (addr[0], self.rtu_port))
        self._inc_stat('h03_sent')
        ctrl_name = CONTROL_TYPE_NAMES.get(ctrl_type, f"Type{ctrl_type}")
        logger.info(f"H03 -> RTU:{rtu_id} {ctrl_name} INV{dev_num} val={value}")
        return True

    # ----- interactive menu -----

    def _interactive_menu(self):
        while self.running:
            try:
                ftp_status = "ON" if self.ftp.is_running() else "OFF"
                print("\n" + "=" * 60)
                print("  RTU Production Server - Control Menu")
                print("=" * 60)
                print("  [1] Inverter ON/OFF    [5] Control Init")
                print("  [2] Active Power       [6] Control Check")
                print("  [3] Power Factor       [7] Model Info")
                print("  [4] Reactive Power     [8] IV Scan")
                print("  [9] RTU Info           [R] RTU Reboot")
                print("-" * 60)
                print(f"  [F] FTP Server [{ftp_status}]  [S] Stats  [Q] Quit")
                print("-" * 60)

                choice = input("Select: ").strip().lower()

                if choice == 'q':
                    self.stop()
                    break
                elif choice in ('1','2','3','4','5','6','7','8','9','r'):
                    rtu_id = self._select_rtu()
                    if not rtu_id:
                        continue
                    if choice == '1':
                        dev = int(input("Inverter num: "))
                        val = int(input("0=ON, 1=OFF: "))
                        self._send_h03(rtu_id, CTRL_INV_ON_OFF, DEVICE_INVERTER, dev, val)
                    elif choice == '2':
                        dev = int(input("Inverter num: "))
                        pct = float(input("Power limit % (0-100): "))
                        self._send_h03(rtu_id, CTRL_INV_ACTIVE_POWER, DEVICE_INVERTER, dev, int(pct*10))
                    elif choice == '3':
                        dev = int(input("Inverter num: "))
                        pf = float(input("PF (-1.0~1.0): "))
                        self._send_h03(rtu_id, CTRL_INV_POWER_FACTOR, DEVICE_INVERTER, dev, int(pf*1000))
                    elif choice == '4':
                        dev = int(input("Inverter num: "))
                        rp = float(input("Reactive % (-100~100): "))
                        self._send_h03(rtu_id, CTRL_INV_REACTIVE_POWER, DEVICE_INVERTER, dev, int(rp*10))
                    elif choice == '5':
                        dev = int(input("Inverter num: "))
                        self._send_h03(rtu_id, CTRL_INV_CONTROL_INIT, DEVICE_INVERTER, dev, 0)
                    elif choice == '6':
                        dev = int(input("Inverter num: "))
                        self._send_h03(rtu_id, CTRL_INV_CONTROL_CHECK, DEVICE_INVERTER, dev, 0)
                    elif choice == '7':
                        dev = int(input("Inverter num: "))
                        self._send_h03(rtu_id, CTRL_INV_MODEL, DEVICE_INVERTER, dev, 0)
                    elif choice == '8':
                        dev = int(input("Inverter num: "))
                        self._send_h03(rtu_id, CTRL_INV_IV_SCAN, DEVICE_INVERTER, dev, 1)
                    elif choice == '9':
                        self._send_h03(rtu_id, CTRL_RTU_INFO, DEVICE_RTU, 0, 0)
                    elif choice == 'r':
                        if input("Confirm reboot? (yes/no): ") == 'yes':
                            self._send_h03(rtu_id, CTRL_RTU_REBOOT, DEVICE_RTU, 0, 0)
                elif choice == 'f':
                    if self.ftp.is_running():
                        self.ftp.stop()
                        print("FTP stopped")
                    else:
                        self.ftp.start()
                elif choice == 's':
                    print(f"\n  Stats: {self._get_stats()}")
                    print(f"  DB rows: {self.db.count_rows()}")
                    rtus = self.tracker.get_all()
                    print(f"  Connected RTUs: {len(rtus)}")
                    for rid, info in rtus.items():
                        age = int(time.time() - info['last_seen'])
                        print(f"    RTU {rid}: {info['ip']}:{info['port']} ({age}s ago)")
            except KeyboardInterrupt:
                self.stop()
                break
            except Exception as e:
                print(f"Error: {e}")

    def _select_rtu(self) -> int | None:
        rtus = self.tracker.get_all()
        if not rtus:
            print("No RTU connected.")
            return None
        if len(rtus) == 1:
            return list(rtus.keys())[0]
        print("Connected RTUs:")
        items = list(rtus.items())
        for i, (rid, info) in enumerate(items):
            print(f"  [{i+1}] RTU {rid} at {info['ip']}")
        try:
            idx = int(input("Select: ")) - 1
            return items[idx][0]
        except (ValueError, IndexError):
            return None


# =============================================================================
# Self-test
# =============================================================================

def run_self_test(port: int = 13133):
    """Automated self-test: start server, send test packets, verify DB."""
    import tempfile

    db_path = os.path.join(tempfile.gettempdir(), f"udp_server_test_{port}.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    print(f"\n{'='*60}")
    print(f"  Self-Test on port {port}")
    print(f"  DB: {db_path}")
    print(f"{'='*60}\n")

    server = ProductionUDPServer(
        listen_port=port, db_path=db_path,
        rate_limit_interval=5.0, interactive=False)

    # Start server in background
    server.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.socket.bind(('0.0.0.0', port))
    server.socket.settimeout(0.5)
    server.running = True
    server.stats['start_time'] = time.time()
    threading.Thread(target=server._receive_loop, daemon=True).start()
    time.sleep(0.3)

    test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    test_sock.settimeout(2.0)
    dest = ('127.0.0.1', port)
    results = []

    def check(name, condition):
        status = "PASS" if condition else "FAIL"
        results.append((name, condition))
        print(f"  [{status}] {name}")

    try:
        # --- Test 1: H01 inverter packet ---
        rtu_id = 99999
        seq1 = 10001
        timestamp = int(time.time())
        header = struct.pack(HEADER_FORMAT, VERSION_H01, seq1, rtu_id, timestamp,
                             DEVICE_INVERTER, 1, 4, 0, INV_BODY_BASIC)
        body = struct.pack(INV_BASIC_FORMAT,
                           3200, 800, 25600,  # pv: 320V, 8A, 25600W
                           220, 221, 222,      # ac voltages
                           100, 101, 102,      # ac currents
                           25000,              # ac power
                           980, 600,           # pf=0.98, freq=60.0
                           1234567,            # energy
                           0x03, 0, 0, 0)      # status=on_grid
        pkt1 = header + body
        test_sock.sendto(pkt1, dest)
        try:
            ack, _ = test_sock.recvfrom(64)
            check("H01 ACK received", len(ack) >= 3 and ack[0] == VERSION_H02)
        except socket.timeout:
            check("H01 ACK received", False)

        time.sleep(0.2)

        # --- Test 2: H01 relay packet ---
        seq2 = 10002
        header2 = struct.pack(HEADER_FORMAT, VERSION_H01, seq2, rtu_id, timestamp,
                              DEVICE_PROTECTION_RELAY, 1, 1, 0, RELAY_BODY_BASIC_DATA)
        body2 = struct.pack(RELAY_BASIC_FORMAT,
                            220.1, 221.2, 222.3,  # voltages
                            10.5, 10.6, 10.7,      # currents
                            2310.0, 2320.0, 2330.0, # powers
                            6960.0, 0.99, 60.0,     # total, pf, freq
                            100000.0, 50000.0,      # energy
                            0, 0)                    # DO/DI
        pkt2 = header2 + body2
        test_sock.sendto(pkt2, dest)
        try:
            ack2, _ = test_sock.recvfrom(64)
            check("H01 Relay ACK received", len(ack2) >= 3 and ack2[0] == VERSION_H02)
        except socket.timeout:
            check("H01 Relay ACK received", False)

        time.sleep(0.2)

        # --- Test 3: Duplicate detection ---
        test_sock.sendto(pkt1, dest)  # Same seq1
        try:
            ack3, _ = test_sock.recvfrom(64)
            check("Duplicate gets ACK", ack3[0] == VERSION_H02)
        except socket.timeout:
            check("Duplicate gets ACK", False)

        time.sleep(0.2)
        check("Duplicate counter incremented", server._get_stats()['duplicates'] >= 1)

        # --- Test 4: H05 event ---
        seq3 = 10003
        event_msg = b"Communication Restored"
        h05_header = struct.pack(HEADER_FORMAT, VERSION_H05, seq3, rtu_id, timestamp,
                                 DEVICE_RTU, 0, 0, 0, BODY_TYPE_RTU_EVENT)
        h05_body = bytes([len(event_msg)]) + event_msg
        test_sock.sendto(h05_header + h05_body, dest)
        try:
            ack4, _ = test_sock.recvfrom(64)
            check("H05 ACK received", ack4[0] == VERSION_H06)
        except socket.timeout:
            check("H05 ACK received", False)

        time.sleep(0.2)

        # --- Test 5: Rate limiting ---
        seq_rl = 10010
        header_rl = struct.pack(HEADER_FORMAT, VERSION_H01, seq_rl, rtu_id, timestamp,
                                DEVICE_INVERTER, 1, 4, 0, INV_BODY_BASIC)
        test_sock.sendto(header_rl + body, dest)
        try:
            test_sock.recvfrom(64)
        except socket.timeout:
            pass
        time.sleep(0.2)
        check("Rate limiter active", server._get_stats()['rate_limited'] >= 1)

        # --- Test 6: DB verification ---
        time.sleep(0.3)
        counts = server.db.count_rows()
        check("RTU registered in DB", counts['rtu_registry'] >= 1)
        check("Inverter data saved", counts['inverter_data'] >= 1)
        check("Relay data saved", counts['relay_data'] >= 1)
        check("Event saved", counts['event_log'] >= 1)

    finally:
        test_sock.close()

    # Cleanup: remove test RTU from tracker and DB
    server.tracker.remove(rtu_id)
    with server.db._lock:
        for table in ('inverter_data', 'relay_data', 'event_log'):
            server.db.conn.execute(f"DELETE FROM {table} WHERE rtu_id=?", (rtu_id,))
        server.db.conn.execute("DELETE FROM rtu_registry WHERE rtu_id=?", (rtu_id,))
        server.db.conn.commit()
    print(f"  [CLEANUP] Test RTU {rtu_id} removed from DB")

    server.stop()

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{'='*60}")
    print(f"  Results: {passed}/{total} passed")
    print(f"{'='*60}")

    if os.path.exists(db_path):
        os.remove(db_path)

    return passed == total


# =============================================================================
# main
# =============================================================================

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    parser = argparse.ArgumentParser(description='RTU Production UDP Server')
    parser.add_argument('--port', type=int, default=DEFAULT_SERVER_PORT,
                        help=f'UDP listen port (default: {DEFAULT_SERVER_PORT})')
    parser.add_argument('--rtu-port', type=int, default=DEFAULT_RTU_LOCAL_PORT,
                        help=f'RTU port (default: {DEFAULT_RTU_LOCAL_PORT})')
    parser.add_argument('--ftp-port', type=int, default=DEFAULT_BUILTIN_FTP_PORT)
    parser.add_argument('--ftp-root', type=str, default=DEFAULT_FTP_ROOT_DIR)
    parser.add_argument('--db-path', type=str,
                        default=os.path.join(script_dir, 'udp_server.db'))
    parser.add_argument('--retention-days', type=int, default=30)
    parser.add_argument('--rate-limit', type=float, default=10.0,
                        help='Min seconds between H01 for same device (default: 10)')
    parser.add_argument('--log-level', type=str, default='INFO')
    parser.add_argument('--log-dir', type=str,
                        default=os.path.join(script_dir, 'logs'))
    parser.add_argument('--interactive', action='store_true',
                        help='Enable interactive H03 control menu')
    parser.add_argument('--self-test', action='store_true',
                        help='Run automated self-test on port 13133')

    args = parser.parse_args()

    setup_logging(log_dir=args.log_dir, level=args.log_level)

    if args.self_test:
        success = run_self_test(port=13133)
        sys.exit(0 if success else 1)

    server = ProductionUDPServer(
        listen_port=args.port,
        rtu_port=args.rtu_port,
        ftp_port=args.ftp_port,
        ftp_root=args.ftp_root,
        db_path=args.db_path,
        retention_days=args.retention_days,
        rate_limit_interval=args.rate_limit,
        interactive=args.interactive,
    )

    signal.signal(signal.SIGINT, lambda s, f: server.stop())

    server.start()


if __name__ == '__main__':
    main()
