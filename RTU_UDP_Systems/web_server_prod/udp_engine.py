"""
UDP Packet Receive/Parse/ACK Engine for RTU Dashboard (Production)
Supports 100+ RTUs simultaneously. Parses H01/H04/H05/H08,
sends H02/H06 ACKs, and H03/H07 control/firmware commands.

Production improvements over web_server/udp_engine.py:
- DuplicateDetector: dedup (rtu_id, seq) within 5-minute window
- RateLimiter: per-device minimum 10s between H01 packets
- Socket lock: thread-safe sendto()
- ACK error handling: all sendto() wrapped in try/except
- IV scan TTL: auto-cleanup scan data older than 1 hour
- RTU timeout: get_stale_rtus() helper
- Stats: 'duplicates' and 'rate_limited' counters
- ACK-first pattern: ACK sent before body parsing, consistently
- Duplicate handling: always ACK (RTU needs it), skip callbacks if dup
"""

import sys
import os
import socket
import struct
import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.protocol_constants import *

logger = logging.getLogger(__name__)


# ============================================================================
# H01 Data Sanity Check (warn only, never block)
# ============================================================================

_SANITY_RANGES = {
    'ac_power': (-500000, 500000),      # ±500kW
    'pv_power': (0, 500000),            # 0~500kW
    'r_voltage': (0, 1000),             # 0~1000V
    's_voltage': (0, 1000),
    't_voltage': (0, 1000),
    'frequency': (0, 100),              # 0~100Hz
    'power_factor': (-1.1, 1.1),        # -1.1~1.1
}

_MODEL_NAME_MANUFACTURERS = (
    ("huawei", "Huawei"),
    ("sun2000", "Huawei"),
    ("huaweinew", "Huawei"),
    ("kstar", "Kstar"),
    ("sungrow", "Sungrow"),
    ("sg", "Sungrow"),
    ("solarize", "Solarize"),
    ("senergy", "Senergy"),
    ("growatt", "Growatt"),
    ("solis", "Solis"),
    ("sofar", "Sofar"),
    ("sunways", "Sunways"),
    ("cps", "CPS"),
    ("ekos", "Ekos"),
)


def _infer_inverter_manufacturer(model: int | None = None, model_name: str | None = None) -> str | None:
    """Infer inverter manufacturer from known model ids or the reported model name."""
    if model in INV_MODEL_NAMES:
        return INV_MODEL_NAMES[model].split()[0]

    raw_name = str(model_name or "").strip()
    lowered = raw_name.lower()
    if lowered:
        for token, manufacturer in _MODEL_NAME_MANUFACTURERS:
            if token in lowered:
                return manufacturer

        first_token = raw_name.replace("_", " ").replace("-", " ").split()
        if first_token:
            cleaned = "".join(ch for ch in first_token[0] if ch.isalnum())
            if cleaned:
                return cleaned[:1].upper() + cleaned[1:]

    return None


def _sanity_check(parsed: dict, rtu_id: int, dev_num: int):
    """Log warning for out-of-range inverter values. Never blocks data."""
    for field, (lo, hi) in _SANITY_RANGES.items():
        val = parsed.get(field)
        if val is not None and not (lo <= val <= hi):
            logger.warning(
                f"Sanity: RTU:{rtu_id} INV{dev_num} {field}={val} out of [{lo},{hi}]")


# ============================================================================
# DuplicateDetector
# ============================================================================

class DuplicateDetector:
    """Track (rtu_id, seq) pairs for 5 minutes to detect duplicate packets.
    Lazy cleanup runs at most once every 60 seconds."""

    TTL = 300  # 5 minutes
    CLEANUP_INTERVAL = 60  # seconds

    def __init__(self):
        self._seen: dict[tuple[int, int], float] = {}  # (rtu_id, seq) -> timestamp
        self._last_cleanup: float = time.time()
        self._lock = threading.Lock()

    def is_duplicate(self, rtu_id: int, seq: int) -> bool:
        """Return True if this (rtu_id, seq) was seen within the TTL window."""
        now = time.time()
        key = (rtu_id, seq)

        with self._lock:
            # Lazy cleanup: 점진적 삭제로 메모리 재할당 최소화
            if now - self._last_cleanup > self.CLEANUP_INTERVAL:
                cutoff = now - self.TTL
                expired_keys = [k for k, t in self._seen.items() if t <= cutoff]
                for k in expired_keys:
                    del self._seen[k]
                self._last_cleanup = now

            if key in self._seen:
                return True
            self._seen[key] = now
            return False


# ============================================================================
# RateLimiter
# ============================================================================

class RateLimiter:
    """Per (rtu_id, dev_type, dev_num) rate limiter.
    Enforces minimum 10 seconds between H01 packets for the same device."""

    MIN_INTERVAL = 10  # seconds

    def __init__(self):
        self._last: dict[tuple[int, int, int], float] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.time()

    def is_allowed(self, rtu_id: int, dev_type: int, dev_num: int) -> bool:
        """Return True if enough time has passed since last packet for this device."""
        now = time.time()
        key = (rtu_id, dev_type, dev_num)

        with self._lock:
            # Periodic cleanup of stale entries (every 300s)
            if now - self._last_cleanup > 300:
                self._last_cleanup = now
                stale = [k for k, t in self._last.items() if now - t > 300]
                for k in stale:
                    del self._last[k]

            last_time = self._last.get(key, 0.0)
            if now - last_time < self.MIN_INTERVAL:
                return False
            self._last[key] = now
            return True


# ============================================================================
# RTUState
# ============================================================================

@dataclass
class RTUState:
    """Per-RTU state tracking."""
    rtu_id: int = 0
    ip: str = ""
    port: int = 0
    last_seen: float = 0.0
    devices: dict = field(default_factory=dict)  # {(dev_type, dev_num): last_parsed_data_dict}
    connected: bool = False
    avg_interval: float = 0.0  # observed avg seconds between H01 batches
    _prev_seen: float = 0.0   # previous last_seen for interval calc
    rtu_info: dict = field(default_factory=dict)  # {model, phone, serial, firmware}
    dev_caps: dict = field(default_factory=dict)  # {dev_num: {iv_scan: bool, der_avm: bool}}


# ============================================================================
# UDPEngine (Production)
# ============================================================================

class UDPEngine:
    """UDP engine that receives, parses, and ACKs RTU packets."""

    IV_SCAN_TTL = 3600  # 1 hour
    DEVICE_STALE_MIN_AGE = 20  # seconds

    def __init__(self, listen_port: int = DEFAULT_SERVER_PORT,
                 rtu_port: int = DEFAULT_RTU_LOCAL_PORT):
        self.listen_port = listen_port
        self.rtu_port = rtu_port
        self.sock: Optional[socket.socket] = None
        self.rtu_registry: dict[int, RTUState] = {}
        self.running = False
        self._lock = threading.Lock()
        self._sock_lock = threading.Lock()  # Dedicated lock for sendto()

        self._dup_detector = DuplicateDetector()
        self._rate_limiter = RateLimiter()

        self.stats = {
            'h01_received': 0,
            'h02_sent': 0,
            'h03_sent': 0,
            'h04_received': 0,
            'h05_received': 0,
            'h06_sent': 0,
            'h08_received': 0,
            'duplicates': 0,
            'rate_limited': 0,
            'keepalive_sent': 0,
            'start_time': 0,
        }

        # IV Scan data storage: {rtu_id: {dev_num, model, total_strings, timestamp, strings: {str_num: [(v,i),...]}}}
        self.iv_scan_data: dict[int, dict] = {}

        # Callbacks set by main.py
        self.on_h01_data: Optional[Callable] = None   # (rtu_id, device_key, parsed_data)
        self.on_event: Optional[Callable] = None       # (rtu_id, event_type, detail)
        self.on_h04: Optional[Callable] = None         # (rtu_id, parsed)
        self.on_h05: Optional[Callable] = None         # (rtu_id, parsed)
        self.on_raw_packet: Optional[Callable] = None  # (rtu_id, info_dict)
        self.on_control_status: Optional[Callable] = None   # (rtu_id, dev_num, data_dict)
        self.on_control_monitor: Optional[Callable] = None  # (rtu_id, dev_num, data_dict)
        self.on_h01_cycle_complete: Optional[Callable] = None  # (rtu_id) — called after H01 cycle

        # H01 cycle detection: timer per RTU to detect end of H01 burst
        self._h01_cycle_timers: dict[int, threading.Timer] = {}
        self._h01_cycle_lock = threading.Lock()

        # NAT keepalive: send H03 RTU info request between H01 cycles
        self.keepalive_enabled = True
        self.keepalive_interval = 30  # seconds between keepalives
        self.keepalive_margin = 10    # don't send within ±N seconds of expected H01
        self._keepalive_seqs: set = set()  # track keepalive sequences to distinguish from manual

        self.seq = 1000
        self._seq_lock = threading.Lock()

    def _next_seq(self) -> int:
        with self._seq_lock:
            self.seq = (self.seq % 65535) + 1
            return self.seq

    def _safe_sendto(self, data: bytes, addr: tuple) -> bool:
        """Thread-safe sendto with error handling. Returns True on success."""
        try:
            with self._sock_lock:
                if self.sock:
                    self.sock.sendto(data, addr)
                    return True
                return False
        except Exception as e:
            logger.error(f"sendto {addr} failed: {e}")
            return False

    # =========================================================================
    # Start / Stop
    # =========================================================================

    def start(self):
        """Bind UDP socket and start receive thread."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('0.0.0.0', self.listen_port))
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)  # 1MB for ~1600 RTUs
        self.sock.settimeout(0.5)
        self.running = True
        self.stats['start_time'] = time.time()

        recv_thread = threading.Thread(target=self._recv_loop, daemon=True, name="udp-recv")
        recv_thread.start()

        if self.keepalive_enabled:
            ka_thread = threading.Thread(target=self._keepalive_loop, daemon=True, name="udp-keepalive")
            ka_thread.start()
            logger.info(f"NAT keepalive enabled: H03 every {self.keepalive_interval}s "
                        f"(margin={self.keepalive_margin}s)")

        logger.info(f"UDPEngine started on port {self.listen_port}")

    def stop(self):
        """Stop the engine and close the socket."""
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        logger.info("UDPEngine stopped")

    # =========================================================================
    # Receive Loop
    # =========================================================================

    def _recv_loop(self):
        """Main receive loop running in its own thread."""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
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
                    logger.debug(f"Unknown packet version {version} from {addr}")
            except Exception as e:
                logger.error(f"Error handling packet v={version} from {addr}: {e}")

    # =========================================================================
    # H01 - Periodic Data
    # =========================================================================

    def _handle_h01(self, data: bytes, addr: tuple):
        """Parse H01 inverter/relay data, update RTUState, call callback."""
        if len(data) < HEADER_SIZE:
            return

        header = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
        version = header[0]
        seq = header[1]
        rtu_id = header[2]
        timestamp = header[3]
        dev_type = header[4]
        dev_num = header[5]
        model = header[6]
        backup = header[7]
        body_type = header[8]

        # Update RTU registry (always, even for duplicates — updates last_seen/addr)
        with self._lock:
            if rtu_id not in self.rtu_registry:
                self.rtu_registry[rtu_id] = RTUState(
                    rtu_id=rtu_id, ip=addr[0], port=addr[1],
                    last_seen=time.time(), connected=True
                )
                logger.info(f"New RTU registered: {rtu_id} from {addr[0]}:{addr[1]}")
            else:
                state = self.rtu_registry[rtu_id]
                state.ip = addr[0]
                state.port = addr[1]
                now = time.time()
                # Track interval between H01 batches (EMA)
                # Use device_number==1 as batch marker to avoid intra-batch gaps
                if state._prev_seen > 0:
                    gap = now - state._prev_seen
                    if gap > 30:  # ignore intra-batch gaps (< 30s)
                        if state.avg_interval > 0:
                            state.avg_interval = state.avg_interval * 0.5 + gap * 0.5
                        else:
                            state.avg_interval = gap
                        state._prev_seen = now
                else:
                    state._prev_seen = now
                state.last_seen = now
                state.connected = True

        self.stats['h01_received'] += 1

        # ACK-first: Send H02 ACK BEFORE parsing body
        ack = struct.pack(H02_FORMAT, VERSION_H02, seq, RESPONSE_SUCCESS)
        if self._safe_sendto(ack, addr):
            self.stats['h02_sent'] += 1

        # Broadcast raw packet info for H1 Log tab
        if self.on_raw_packet:
            body = data[HEADER_SIZE:]
            exp = self._expected_body_size(dev_type, body_type, body)
            combined = exp > 0 and len(body) > exp + HEADER_SIZE
            try:
                self.on_raw_packet(rtu_id, {
                    'seq': seq, 'dev_type': dev_type, 'dev_num': dev_num,
                    'backup': backup, 'body_size': len(body),
                    'combined': combined, 'raw_hex': data.hex(),
                    'timestamp': timestamp, 'src_addr': f"{addr[0]}:{addr[1]}",
                    'body_type': body_type, 'model': model,
                })
            except Exception:
                pass

        # Backup packets: skip duplicate detection and rate limiting
        # (backup data must always be stored — it's recovery from past outage)
        is_backup = (backup == 1)

        if not is_backup:
            # Duplicate check: always ACK (above), but skip processing if dup
            if self._dup_detector.is_duplicate(rtu_id, seq):
                self.stats['duplicates'] += 1
                logger.debug(f"Duplicate H01 from RTU:{rtu_id} seq={seq}, ACKed but skipped")
                if self.on_event:
                    self.on_event(rtu_id, "duplicate_dropped",
                        f"Duplicate seq={seq} dev={dev_type}/{dev_num}")
                return

            # Rate limit check
            if not self._rate_limiter.is_allowed(rtu_id, dev_type, dev_num):
                self.stats['rate_limited'] += 1
                logger.debug(f"Rate-limited H01 from RTU:{rtu_id} dev={dev_type}/{dev_num}")
                if self.on_event:
                    self.on_event(rtu_id, "rate_limited",
                        f"Rate-limited dev={dev_type}/{dev_num}")
                return

        # Skip parsing if body_type is negative (comm fail, error, etc.)
        if body_type < 0:
            device_key = (dev_type, dev_num)
            _is_nighttime = (body_type == -4)
            parsed = {
                'device_number': dev_num,
                'model': model,
                'body_type': body_type,
                'backup': backup,
                'timestamp': timestamp,
                'error': not _is_nighttime,
            }
            with self._lock:
                self.rtu_registry[rtu_id].devices[device_key] = parsed
            if self.on_event:
                if _is_nighttime:
                    self.on_event(rtu_id, "nighttime_standby",
                                  f"Device {dev_type}/{dev_num}: NIGHTTIME_STANDBY")
                else:
                    body_names = {-3: "ZEE_SKIP", -2: "PACKET_ERROR", -1: "COMM_FAIL"}
                    detail = body_names.get(body_type, f"ERROR({body_type})")
                    self.on_event(rtu_id, "comm_fail", f"Device {dev_type}/{dev_num}: {detail}")
            return

        body = data[HEADER_SIZE:]

        # RI Power combined packet detection:
        # When body contains multiple records (current + backup), each record
        # is a full 98-byte frame (20B header + 78B body) appended after the
        # first body. Detect by checking if body is larger than expected.
        records = []  # list of (header_dict, body_bytes) to process

        expected_body_size = self._expected_body_size(dev_type, body_type, body)
        if expected_body_size > 0 and len(body) > expected_body_size + HEADER_SIZE:
            # Combined packet: first record uses outer header + first body chunk
            records.append({
                'body': body[:expected_body_size],
                'dev_type': dev_type, 'dev_num': dev_num,
                'model': model, 'backup': backup,
                'timestamp': timestamp, 'body_type': body_type,
                'seq': seq,
            })
            # Subsequent records: each is HEADER_SIZE + body_size
            remaining = body[expected_body_size:]
            while len(remaining) >= HEADER_SIZE + 44:  # at least header + min body
                prev_len = len(remaining)
                sub_header = struct.unpack(HEADER_FORMAT, remaining[:HEADER_SIZE])
                sub_body_type = sub_header[8]
                sub_body = remaining[HEADER_SIZE:]
                sub_expected = self._expected_body_size(sub_header[4], sub_body_type, sub_body)
                if sub_expected <= 0:
                    sub_expected = len(sub_body)  # take all remaining
                sub_body = sub_body[:sub_expected]
                records.append({
                    'body': sub_body,
                    'dev_type': sub_header[4], 'dev_num': sub_header[5],
                    'model': sub_header[6], 'backup': sub_header[7],
                    'timestamp': sub_header[3], 'body_type': sub_body_type,
                    'seq': sub_header[1],
                })
                remaining = remaining[HEADER_SIZE + sub_expected:]
                if len(remaining) >= prev_len:  # 진전이 없으면 무한루프 방지
                    logger.warning(f"Combined packet parse: no progress, breaking loop (remaining={len(remaining)})")
                    break
                logger.info(f"Combined packet from RTU:{rtu_id}: split backup record "
                           f"dev={sub_header[4]}/{sub_header[5]} BK={sub_header[7]} "
                           f"ts={sub_header[3]}")
        else:
            # Normal single-record packet
            records.append({
                'body': body,
                'dev_type': dev_type, 'dev_num': dev_num,
                'model': model, 'backup': backup,
                'timestamp': timestamp, 'body_type': body_type,
                'seq': seq,
            })

        # Process all records
        for rec in records:
            parsed = None
            if rec['dev_type'] == DEVICE_INVERTER:
                parsed = self._parse_inverter(rec['body'], rec['body_type'], rec['dev_num'], rec['model'])
            elif rec['dev_type'] == DEVICE_PROTECTION_RELAY:
                parsed = self._parse_relay(rec['body'], rec['dev_num'], rec['model'])
            elif rec['dev_type'] == DEVICE_WEATHER_STATION:
                parsed = self._parse_weather(rec['body'], rec['dev_num'], rec['model'])

            if parsed is not None:
                parsed['backup'] = rec['backup']
                parsed['timestamp'] = rec['timestamp']
                parsed['raw_hex'] = data.hex() if rec is records[0] else ''
                # Convert epoch timestamp to KST string for DB storage
                try:
                    from datetime import datetime, timezone, timedelta
                    KST = timezone(timedelta(hours=9))
                    dt = datetime.fromtimestamp(rec['timestamp'], tz=KST)
                    parsed['original_timestamp'] = dt.strftime('%Y-%m-%d %H:%M:%S')
                except (OSError, ValueError, OverflowError):
                    parsed['original_timestamp'] = None

                device_key = (rec['dev_type'], rec['dev_num'])
                # Only update live device view if data is newer than existing
                with self._lock:
                    existing = self.rtu_registry[rtu_id].devices.get(device_key)
                    if existing is None or rec['timestamp'] >= existing.get('timestamp', 0):
                        # Skip if backup flag is set (recovery data — save to DB only)
                        if not rec['backup']:
                            self.rtu_registry[rtu_id].devices[device_key] = parsed

                # Sanity check + timestamp validation
                if rec['dev_type'] == DEVICE_INVERTER:
                    _sanity_check(parsed, rtu_id, rec['dev_num'])
                server_now = time.time()
                if abs(server_now - rec['timestamp']) > 3600:
                    logger.warning(
                        f"Timestamp drift: RTU:{rtu_id} pkt={rec['timestamp']:.0f} "
                        f"server={server_now:.0f} diff={server_now - rec['timestamp']:.0f}s")
                    parsed['timestamp'] = server_now
                    parsed['timestamp_corrected'] = True

                if self.on_h01_data:
                    self.on_h01_data(rtu_id, device_key, parsed)

                # H01 cycle detection: reset 2-second timer per RTU
                if not rec.get('backup_flag') and self.on_h01_cycle_complete:
                    self._reset_h01_cycle_timer(rtu_id)

    def _reset_h01_cycle_timer(self, rtu_id: int):
        """Reset the 2-second timer for H01 cycle completion detection.
        After last H01 in a burst, timer fires and sends H03(13) per inverter."""
        with self._h01_cycle_lock:
            old = self._h01_cycle_timers.get(rtu_id)
            if old:
                old.cancel()
            t = threading.Timer(2.0, self._on_h01_cycle_complete, args=[rtu_id])
            t.daemon = True
            t.start()
            self._h01_cycle_timers[rtu_id] = t

    def _on_h01_cycle_complete(self, rtu_id: int):
        """Called 2 seconds after last H01 — trigger control check for this RTU."""
        with self._h01_cycle_lock:
            self._h01_cycle_timers.pop(rtu_id, None)
        if self.on_h01_cycle_complete:
            try:
                self.on_h01_cycle_complete(rtu_id)
                logger.info(f"H01 cycle complete for RTU:{rtu_id} → control check sent")
            except Exception as e:
                logger.error(f"H01 cycle complete callback error: {e}")

    def _expected_body_size(self, dev_type: int, body_type: int, body: bytes) -> int:
        """Calculate expected body size for a single record.
        Returns 0 if unknown (caller should treat as single record)."""
        if dev_type == DEVICE_INVERTER:
            size = INV_BASIC_SIZE  # 44
            offset = INV_BASIC_SIZE
            if body_type in (INV_BODY_BASIC_MPPT, INV_BODY_BASIC_MPPT_STRING):
                if offset < len(body):
                    mppt_count = body[offset]
                    size += 1 + mppt_count * 4
                    offset = size
            if body_type in (INV_BODY_BASIC_STRING, INV_BODY_BASIC_MPPT_STRING):
                if offset < len(body):
                    str_count = body[offset]
                    size += 1 + str_count * 2
            return size
        elif dev_type == DEVICE_PROTECTION_RELAY:
            return RELAY_BASIC_SIZE
        return 0

    def _parse_inverter(self, body: bytes, body_type: int, dev_num: int, model: int) -> dict | None:
        """Parse inverter H01 body."""
        if len(body) < INV_BASIC_SIZE:
            return None

        basic = struct.unpack(INV_BASIC_FORMAT, body[:INV_BASIC_SIZE])

        # Fields: pv_voltage(0.1V), pv_current(0.01A), pv_power(W),
        #   r_voltage(V), s_voltage(V), t_voltage(V),
        #   r_current(0.1A), s_current(0.1A), t_current(0.1A),
        #   ac_power(W), power_factor(0.001), frequency(0.1Hz),
        #   cumulative_energy(Wh), status, alarm1, alarm2, alarm3
        parsed = {
            'device_number': dev_num,
            'model': model,
            'manufacturer': _infer_inverter_manufacturer(model=model),
            'body_type': body_type,
            'pv_voltage': basic[0],
            'pv_current': basic[1],
            'pv_power': basic[2],
            'r_voltage': basic[3],
            's_voltage': basic[4],
            't_voltage': basic[5],
            'r_current': basic[6],
            's_current': basic[7],
            't_current': basic[8],
            'ac_power': basic[9],
            'power_factor': basic[10] / 1000.0,
            'frequency': basic[11] / 10.0,
            'cumulative_energy': basic[12],
            'status': basic[13],
            'alarm1': basic[14],
            'alarm2': basic[15],
            'alarm3': basic[16],
        }

        offset = INV_BASIC_SIZE

        # Parse MPPT data if present
        if body_type in (INV_BODY_BASIC_MPPT, INV_BODY_BASIC_MPPT_STRING):
            if offset < len(body):
                mppt_count = body[offset]
                offset += 1
                mppts = []
                for _ in range(mppt_count):
                    if offset + 4 <= len(body):
                        v, c = struct.unpack('>HH', body[offset:offset + 4])
                        mppts.append({'voltage': v / 10.0, 'current': c / 10.0})
                        offset += 4
                parsed['mppt'] = mppts

        # Parse String data if present
        if body_type in (INV_BODY_BASIC_STRING, INV_BODY_BASIC_MPPT_STRING):
            if offset < len(body):
                str_count = body[offset]
                offset += 1
                strings = []
                for _ in range(str_count):
                    if offset + 2 <= len(body):
                        c = struct.unpack('>H', body[offset:offset + 2])[0]
                        strings.append(c / 10.0)
                        offset += 2
                parsed['strings'] = strings

        return parsed

    def _parse_relay(self, body: bytes, dev_num: int, model: int) -> dict | None:
        """Parse relay H01 body."""
        if len(body) < RELAY_BASIC_SIZE:
            return None

        vals = struct.unpack(RELAY_BASIC_FORMAT, body[:RELAY_BASIC_SIZE])

        return {
            'device_number': dev_num,
            'model': model,
            'r_voltage': vals[0],
            's_voltage': vals[1],
            't_voltage': vals[2],
            'r_current': vals[3],
            's_current': vals[4],
            't_current': vals[5],
            'r_power': vals[6],
            's_power': vals[7],
            't_power': vals[8],
            'total_active_power': vals[9],
            'avg_power_factor': vals[10],
            'frequency': vals[11],
            'received_energy': vals[12],
            'sent_energy': vals[13],
            'do_status': vals[14],
            'di_status': vals[15],
        }

    def _parse_weather(self, body: bytes, dev_num: int, model: int) -> dict | None:
        """Parse weather station H01 body (SEM5046)."""
        # WEATHER_BASIC_FORMAT = '>hhHhhhhHhHhhh' = 26 bytes
        fmt = WEATHER_BASIC_FORMAT
        size = struct.calcsize(fmt)
        if len(body) < size:
            return None

        vals = struct.unpack(fmt, body[:size])

        return {
            'device_number': dev_num,
            'device_type': DEVICE_WEATHER_STATION,
            'model': model,
            'air_temp': vals[0] / 10.0,
            'air_humidity': vals[1] / 10.0,
            'air_pressure': vals[2] / 10.0,
            'wind_speed': vals[3] / 10.0,
            'wind_direction': vals[4],
            'module_temp_1': vals[5] / 10.0,
            'horizontal_radiation': vals[6],
            'horizontal_accum': vals[7] / 100.0,
            'inclined_radiation': vals[8],
            'inclined_accum': vals[9] / 100.0,
            'module_temp_2': vals[10] / 10.0,
            'module_temp_3': vals[11] / 10.0,
            'module_temp_4': vals[12] / 10.0,
        }

    # =========================================================================
    # H04 - Control Response
    # =========================================================================

    def _handle_h04(self, data: bytes, addr: tuple):
        """Parse H04 control response from RTU."""
        if len(data) < H04_SIZE:
            return

        vals = struct.unpack(H04_FORMAT, data[:H04_SIZE])
        parsed = {
            'version': vals[0],
            'sequence': vals[1],
            'control_type': vals[2],
            'device_type': vals[3],
            'device_number': vals[4],
            'control_value': vals[5],
            'response': vals[6],
        }
        self.stats['h04_received'] += 1

        ctrl_name = CONTROL_TYPE_NAMES.get(parsed['control_type'], f"Type{parsed['control_type']}")
        resp_str = "SUCCESS" if parsed['response'] == 0 else f"FAIL({parsed['response']})"
        logger.info(f"H04 from {addr}: {ctrl_name} -> {resp_str}")

        # Identify RTU from address
        rtu_id = self._rtu_id_from_addr(addr)
        seq = parsed.get('sequence', 0)
        is_keepalive = seq in self._keepalive_seqs

        if rtu_id and is_keepalive:
            # Keepalive response: debug log only, don't spam events/control tab
            self._keepalive_seqs.discard(seq)
            logger.debug(f"Keepalive from RTU:{rtu_id}: {resp_str}")
        elif rtu_id:
            # Normal control response: send to control tab
            if self.on_h04:
                self.on_h04(rtu_id, parsed)

    # =========================================================================
    # H05 - Event / Heartbeat
    # =========================================================================

    def _handle_h05(self, data: bytes, addr: tuple):
        """Parse H05 event/heartbeat from RTU."""
        if len(data) < HEADER_SIZE:
            return

        header = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
        rtu_id = header[2]
        seq = header[1]
        body_type = header[8]
        dev_num = header[5]

        # Update registry
        with self._lock:
            if rtu_id not in self.rtu_registry:
                self.rtu_registry[rtu_id] = RTUState(
                    rtu_id=rtu_id, ip=addr[0], port=addr[1],
                    last_seen=time.time(), connected=True
                )
            else:
                state = self.rtu_registry[rtu_id]
                state.ip = addr[0]
                state.port = addr[1]
                state.last_seen = time.time()
                state.connected = True
                logger.info(f"H05 RTU:{rtu_id} reconnected — keeping existing device data")

        self.stats['h05_received'] += 1

        # ACK-first: Send H06 ACK BEFORE parsing body
        ack = struct.pack(H06_FORMAT, VERSION_H06, seq, RESPONSE_SUCCESS)
        if self._safe_sendto(ack, addr):
            self.stats['h06_sent'] += 1

        # Duplicate check: always ACK (above), but skip callbacks/persistence if dup
        if self._dup_detector.is_duplicate(rtu_id, seq):
            self.stats['duplicates'] += 1
            logger.debug(f"Duplicate H05 from RTU:{rtu_id} seq={seq}, ACKed but skipped")
            return

        body = data[HEADER_SIZE:]
        event_type = "h05"
        detail = f"body_type={body_type}"

        if body_type == BODY_TYPE_HEARTBEAT:
            event_type = "heartbeat"
            detail = "Heartbeat ping"

        elif body_type == BODY_TYPE_RTU_EVENT:
            event_type = "rtu_event"
            if len(body) > 0:
                event_len = body[0]
                if len(body) > event_len:
                    detail = body[1:1 + event_len].decode('utf-8', errors='ignore')

        elif body_type == BODY_TYPE_RTU_INFO:
            event_type = "rtu_info"
            pos = 0
            fields = ['model', 'phone', 'serial', 'firmware']
            info_parts = []
            for field_name in fields:
                if pos < len(body):
                    size = body[pos]
                    pos += 1
                    if pos + size <= len(body):
                        value = body[pos:pos + size].decode('utf-8', errors='ignore')
                        info_parts.append(f"{field_name}={value}")
                        pos += size
            detail = ", ".join(info_parts) if info_parts else "RTU info (empty)"
            # Store rtu_info in RTUState
            if rtu_id in self.rtu_registry:
                info_dict = {}
                for part in info_parts:
                    k, _, v = part.partition('=')
                    info_dict[k] = v
                with self._lock:
                    self.rtu_registry[rtu_id].rtu_info = info_dict

        elif body_type == BODY_TYPE_POWER_OUTAGE:
            event_type = "power_outage"
            if len(body) >= 4:
                outage_time = struct.unpack('>I', body[:4])[0]
                detail = f"Power outage at ts={outage_time}"

        elif body_type == BODY_TYPE_POWER_RESTORE:
            event_type = "power_restore"
            if len(body) >= 12:
                outage_ts, restore_ts, duration = struct.unpack('>III', body[:12])
                detail = f"Power restored: outage={outage_ts}, restore={restore_ts}, duration={duration}s"

        elif body_type == BODY_TYPE_INVERTER_MODEL:
            event_type = "inverter_model"
            pos = 0
            parts = []
            model_name = ""
            serial_number = ""
            for fname in ['model_name', 'serial_number']:
                if pos < len(body):
                    size = body[pos]
                    pos += 1
                    if pos + size <= len(body):
                        value = body[pos:pos + size].decode('utf-8', errors='ignore')
                        if fname == 'model_name':
                            model_name = value.strip()
                        elif fname == 'serial_number':
                            serial_number = value.strip()
                        parts.append(f"{fname}={value}")
                        pos += size
            # Parse capability flags: bit0=iv_scan, bit1=der_avm
            cap_iv = False
            cap_der = False
            if pos < len(body):
                cap = body[pos]
                cap_iv = bool(cap & 0x01)
                cap_der = bool(cap & 0x02)
                parts.append(f"iv_scan={cap_iv}, der_avm={cap_der}")
            with self._lock:
                state = self.rtu_registry.get(rtu_id)
                if state:
                    existing_caps = state.dev_caps.get(dev_num, {})
                    state.dev_caps[dev_num] = {
                        **existing_caps,
                        'iv_scan': cap_iv,
                        'der_avm': cap_der,
                        'model_name': model_name,
                        'serial_number': serial_number,
                        'manufacturer': _infer_inverter_manufacturer(model_name=model_name),
                    }
            detail = f"INV{dev_num}: " + (", ".join(parts) if parts else "empty")

        elif body_type == BODY_TYPE_CONTROL_CHECK:
            event_type = "control_check"
            if len(body) >= 10:
                on_off, pf, op_mode, reactive, active = struct.unpack('>HhHhH', body[:10])
                on_off_str = 'ON' if on_off == 0 else 'OFF'
                detail = (f"INV{dev_num}: {on_off_str}, pf={pf/1000:.3f}, "
                          f"mode={op_mode}, reactive={reactive/10:.1f}%, active={active/10:.1f}%")
                if self.on_control_status:
                    self.on_control_status(rtu_id, dev_num, {
                        'on_off': on_off, 'power_factor': pf,
                        'operation_mode': op_mode,
                        'reactive_power_pct': reactive, 'active_power_pct': active
                    })

        elif body_type == BODY_TYPE_CONTROL_RESULT:
            event_type = "control_result"
            # Body: '>IIIIIIiiii' + '>I' = 44 bytes
            if len(body) >= 44:
                vals = struct.unpack('>IIIIIIiiii', body[:40])
                flags = struct.unpack('>I', body[40:44])[0]
                detail = (f"INV{dev_num}: "
                          f"I={vals[0]/10:.1f}/{vals[1]/10:.1f}/{vals[2]/10:.1f}A, "
                          f"V={vals[3]/10:.1f}/{vals[4]/10:.1f}/{vals[5]/10:.1f}V, "
                          f"P={vals[6]/10:.1f}kW, Q={vals[7]}Var, "
                          f"PF={vals[8]/1000:.3f}, F={vals[9]/10:.1f}Hz, "
                          f"flags=0x{flags:04X}")
                if self.on_control_monitor:
                    self.on_control_monitor(rtu_id, dev_num, {
                        'current_r': vals[0] / 10.0, 'current_s': vals[1] / 10.0,
                        'current_t': vals[2] / 10.0,
                        'voltage_rs': vals[3] / 10.0, 'voltage_st': vals[4] / 10.0,
                        'voltage_tr': vals[5] / 10.0,
                        'active_power_kw': vals[6] / 10.0,
                        'reactive_power_var': vals[7],
                        'power_factor': vals[8] / 1000.0,
                        'frequency': vals[9] / 10.0,
                        'status_flags': flags
                    })
            else:
                detail = f"INV{dev_num}: control result (body too short: {len(body)}B)"

        elif body_type == BODY_TYPE_IV_SCAN_SUCCESS:
            event_type = "iv_scan_success"
            detail = f"INV{dev_num}: IV scan completed"
            model = header[6]
            with self._lock:
                # Clean stale before new scan
                if rtu_id in self.iv_scan_data:
                    old_ts = self.iv_scan_data[rtu_id].get('timestamp', 0)
                    if time.time() - old_ts > self.IV_SCAN_TTL:
                        del self.iv_scan_data[rtu_id]
                self.iv_scan_data[rtu_id] = {
                    'dev_num': dev_num, 'model': model,
                    'total_strings': 0, 'timestamp': time.time(), 'strings': {},
                }

        elif body_type == BODY_TYPE_IV_SCAN_DATA:
            event_type = "iv_scan_data"
            if len(body) >= 3:
                total_str, str_num, point_count = struct.unpack('>BBB', body[:3])
                points = []
                for p in range(point_count):
                    offset = 3 + p * 4
                    if offset + 4 <= len(body):
                        v_raw, i_raw = struct.unpack('>HH', body[offset:offset + 4])
                        points.append({
                            'voltage': round(v_raw * 0.1, 1),
                            'current': round(i_raw * 0.01, 2),
                        })
                received = 0
                has_session = False
                with self._lock:
                    # Clean stale
                    if rtu_id in self.iv_scan_data:
                        old_ts = self.iv_scan_data[rtu_id].get('timestamp', 0)
                        if time.time() - old_ts > self.IV_SCAN_TTL:
                            del self.iv_scan_data[rtu_id]
                    if rtu_id in self.iv_scan_data:
                        self.iv_scan_data[rtu_id]['total_strings'] = total_str
                        self.iv_scan_data[rtu_id]['strings'][str_num] = points
                        received = len(self.iv_scan_data[rtu_id]['strings'])
                        has_session = True

                if has_session:
                    detail = f"INV{dev_num}: IV string {str_num}/{total_str} ({point_count}pts, {received}/{total_str} done)"
                    if received >= total_str:
                        logger.info(f"IV Scan complete: RTU:{rtu_id} INV{dev_num}, {total_str} strings")
                        if self.on_event:
                            self.on_event(rtu_id, "iv_scan_complete",
                                          f"INV{dev_num}: All {total_str} strings received")
                else:
                    detail = f"INV{dev_num}: IV data string {str_num}/{total_str}, {point_count} points (no scan session)"

        logger.info(f"H05 from RTU:{rtu_id} type={event_type}: {detail}")

        # Skip heartbeat from event callbacks
        skip_event = (event_type == "heartbeat")
        if self.on_event and not skip_event:
            self.on_event(rtu_id, event_type, detail)

        if self.on_h05:
            self.on_h05(rtu_id, {
                'body_type': body_type,
                'event_type': event_type,
                'detail': detail,
                'device_number': dev_num,
            })

    # =========================================================================
    # H08 - Firmware Update Response
    # =========================================================================

    def _handle_h08(self, data: bytes, addr: tuple):
        """Parse H08 firmware update response."""
        if len(data) < H08_SIZE:
            return

        version, seq, dev_type, dev_num, response = struct.unpack(H08_FORMAT, data[:H08_SIZE])
        self.stats['h08_received'] += 1

        response_names = {
            0: "SUCCESS (Update started)",
            1: "COMPLETE (Update applied)",
            -1: "ERROR",
            -2: "FTP_CONNECT_FAIL",
            -3: "FTP_LOGIN_FAIL",
            -4: "FTP_DOWNLOAD_FAIL",
            -5: "EXTRACT_FAIL",
            -6: "APPLY_FAIL",
            -7: "BUSY",
            -8: "HASH_FAIL",
        }
        resp_name = response_names.get(response, f"UNKNOWN({response})")
        logger.info(f"H08 from {addr}: seq={seq}, response={resp_name}")

        rtu_id = self._rtu_id_from_addr(addr)
        if rtu_id and self.on_event:
            self.on_event(rtu_id, "firmware_response", resp_name)

    # =========================================================================
    # NAT Keepalive
    # =========================================================================

    def _keepalive_loop(self):
        """Periodically send H03 RTU Info Request to keep NAT mappings alive.

        For each connected RTU, sends H03 (ctrl_type=2, RTU Info) at intervals
        between H01 cycles. Skips sending if:
        - RTU is not connected
        - Within ±margin seconds of expected next H01
        - RTU was seen very recently (within keepalive_interval)
        """
        while self.running:
            try:
                time.sleep(self.keepalive_interval)
                if not self.running:
                    break

                now = time.time()
                with self._lock:
                    rtus = list(self.rtu_registry.items())

                for rtu_id, state in rtus:
                    if not state.connected or not state.ip:
                        continue

                    elapsed = now - state.last_seen
                    interval = state.avg_interval if state.avg_interval > 0 else 60.0

                    # Skip if RTU was seen very recently (just sent H01)
                    if elapsed < self.keepalive_interval * 0.8:
                        continue

                    # Skip if next H01 is expected soon (within margin)
                    time_to_next = interval - elapsed
                    if 0 < time_to_next < self.keepalive_margin:
                        continue

                    # Skip if RTU hasn't been seen for too long (probably offline)
                    if elapsed > interval * 3:
                        continue

                    # Send H03 RTU Info Request (ctrl_type=2, dev_type=0(RTU), dev_num=1)
                    seq = self._next_seq()
                    packet = struct.pack(H03_FORMAT,
                                         VERSION_H03, seq, CTRL_RTU_INFO,
                                         0, 1, 0)  # dev_type=0(RTU), dev_num=1
                    dest_port = state.port if state.port else self.rtu_port
                    if self._safe_sendto(packet, (state.ip, dest_port)):
                        self.stats['keepalive_sent'] += 1
                        self._keepalive_seqs.add(seq)
                        # Limit set size
                        if len(self._keepalive_seqs) > 200:
                            self._keepalive_seqs = set(list(self._keepalive_seqs)[-100:])
                        logger.debug(f"Keepalive H03 sent to RTU:{rtu_id} "
                                     f"({state.ip}:{dest_port}) elapsed={elapsed:.0f}s")
                    else:
                        logger.debug(f"Keepalive H03 failed for RTU:{rtu_id}")

            except Exception as e:
                logger.error(f"Keepalive loop error: {e}")
                time.sleep(5)

    # =========================================================================
    # Send Commands
    # =========================================================================

    def send_h03(self, rtu_id: int, ctrl_type: int, dev_type: int,
                 dev_num: int, value: int) -> bool:
        """Send H03 control command to a specific RTU."""
        with self._lock:
            state = self.rtu_registry.get(rtu_id)
        if not state:
            logger.warning(f"send_h03: RTU {rtu_id} not found in registry")
            return False

        seq = self._next_seq()
        packet = struct.pack(H03_FORMAT,
                             VERSION_H03, seq, ctrl_type, dev_type, dev_num, value)
        # Use actual source port from RTU's last packet (NAT-safe)
        dest_port = state.port if state.port else self.rtu_port
        if self._safe_sendto(packet, (state.ip, dest_port)):
            self.stats['h03_sent'] += 1
            ctrl_name = CONTROL_TYPE_NAMES.get(ctrl_type, f"Type{ctrl_type}")
            logger.info(f"Sent H03 to RTU:{rtu_id} ({state.ip}:{dest_port}): "
                        f"{ctrl_name} dev={dev_type}/{dev_num} value={value}")
            return True
        else:
            logger.error(f"Failed to send H03 to RTU:{rtu_id}")
            return False

    def send_h07(self, rtu_id: int, ftp_host: str, ftp_port: int,
                 ftp_user: str, ftp_pass: str, ftp_path: str, filename: str) -> bool:
        """Send H07 firmware update command to a specific RTU."""
        with self._lock:
            state = self.rtu_registry.get(rtu_id)
        if not state:
            logger.warning(f"send_h07: RTU {rtu_id} not found in registry")
            return False

        seq = self._next_seq()
        packet = bytearray()
        packet.append(VERSION_H07)
        packet.extend(struct.pack('>H', seq))
        packet.append(DEVICE_RTU)
        packet.append(0xFF)

        def add_string(s: str):
            data = s.encode('utf-8')[:64]
            packet.append(len(data))
            packet.extend(data)

        add_string(ftp_host)
        packet.extend(struct.pack('>H', ftp_port))
        add_string(ftp_user)
        add_string(ftp_pass)
        add_string(ftp_path)
        add_string(filename)

        dest_port = state.port if state.port else self.rtu_port
        if self._safe_sendto(bytes(packet), (state.ip, dest_port)):
            logger.info(f"Sent H07 to RTU:{rtu_id} ({state.ip}:{dest_port}): "
                        f"{ftp_user}@{ftp_host}:{ftp_port} {ftp_path}/{filename}")
            return True
        else:
            logger.error(f"Failed to send H07 to RTU:{rtu_id}")
            return False

    # =========================================================================
    # Query Methods
    # =========================================================================

    def get_rtu_list(self) -> list[dict]:
        """Return list of all RTUs with status."""
        now = time.time()
        result = []
        with self._lock:
            for rtu_id, state in self.rtu_registry.items():
                age = now - state.last_seen
                interval = state.avg_interval
                threshold = max(interval * 2, 120) if interval > 0 else 360
                threshold = min(threshold, 660)
                status = "online" if age < threshold else "offline"
                total_solar = 0
                total_grid = 0
                for (dt, dn), data in state.devices.items():
                    if dt == 1:
                        total_solar += data.get('ac_power', 0)
                    elif dt == 4:
                        total_grid += data.get('total_power', 0)
                # Determine RTU type from model name
                model = state.rtu_info.get('model', '')
                if 'SRPV' in model:
                    rtu_type = 'RIP'  # RI Power RTU
                elif model:
                    rtu_type = 'SOLARIZE'
                else:
                    rtu_type = ''  # Unknown (no RTU Info received yet)
                result.append({
                    'rtu_id': rtu_id,
                    'ip': state.ip,
                    'port': state.port,
                    'last_seen': state.last_seen,
                    'status': status,
                    'device_count': len(state.devices),
                    'total_solar_power': total_solar,
                    'total_grid_power': total_grid,
                    'avg_interval': round(state.avg_interval),
                    'rtu_type': rtu_type,
                    'rtu_info': state.rtu_info,
                })
        return result

    def get_rtu_devices(self, rtu_id: int) -> dict | None:
        """Return devices for a specific RTU with latest data."""
        now = time.time()
        with self._lock:
            state = self.rtu_registry.get(rtu_id)
            if state:
                stale_cutoff = max(
                    self.DEVICE_STALE_MIN_AGE,
                    int(state.avg_interval * 2) if state.avg_interval > 0 else 120,
                )
                stale_keys = []
                for device_key, data in state.devices.items():
                    ts = data.get('timestamp') if isinstance(data, dict) else None
                    if not ts:
                        continue
                    if now - ts > stale_cutoff:
                        stale_keys.append(device_key)
                for device_key in stale_keys:
                    del state.devices[device_key]
        if not state:
            return None

        devices = {}
        for (dev_type, dev_num), data in state.devices.items():
            type_name = DEVICE_TYPE_NAMES.get(dev_type, f"Type{dev_type}")
            key = f"{type_name}_{dev_num}"
            dev_entry = {
                'device_type': dev_type,
                'device_number': dev_num,
                'type_name': type_name,
                'data': data,
            }
            # Merge device capabilities (iv_scan, der_avm) from H05(11)
            caps = state.dev_caps.get(dev_num)
            if caps:
                dev_entry['data'] = {**data, **caps}
            devices[key] = dev_entry
        return devices

    def get_iv_scan_data(self, rtu_id: int) -> dict | None:
        """Return latest IV Scan data for an RTU."""
        with self._lock:
            data = self.iv_scan_data.get(rtu_id)
            return dict(data) if data else None  # Return copy

    def cleanup_stale_iv_scans(self):
        """Remove IV scan data older than TTL (called from background task)."""
        now = time.time()
        with self._lock:
            stale = [rid for rid, d in self.iv_scan_data.items()
                     if now - d.get('timestamp', 0) > self.IV_SCAN_TTL]
            for rid in stale:
                del self.iv_scan_data[rid]
        if stale:
            logger.info(f"Cleaned {len(stale)} stale IV scan entries")

    def get_stale_rtus(self, timeout: int = 120) -> list[int]:
        """Return RTU IDs whose last_seen exceeds the timeout (seconds)."""
        now = time.time()
        stale = []
        with self._lock:
            for rtu_id, state in self.rtu_registry.items():
                if now - state.last_seen > timeout:
                    stale.append(rtu_id)
        return stale

    # =========================================================================
    # Helpers
    # =========================================================================

    def _rtu_id_from_addr(self, addr: tuple) -> int | None:
        """Find rtu_id by address (ip, port)."""
        with self._lock:
            for rtu_id, state in self.rtu_registry.items():
                if state.ip == addr[0]:
                    return rtu_id
        return None
