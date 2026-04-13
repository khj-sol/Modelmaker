"""
Async SQLite Database Layer (Production)
Adds: data retention cleanup, WAL checkpoint, body_type in events, limit bounds.
"""

import re
import logging
import asyncio
import time
import aiosqlite
from datetime import datetime, timezone, timedelta

# ALTER TABLE SQL 화이트리스트
_ALLOWED_COL_TYPES = {'TEXT', 'INTEGER', 'REAL', 'BLOB', 'NUMERIC'}
_ALLOWED_COL_PATTERN = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

def _validate_sql_col(col: str, ctype: str):
    """ALTER TABLE에 사용할 컬럼명과 타입을 화이트리스트로 검증."""
    if not _ALLOWED_COL_PATTERN.match(col):
        raise ValueError(f"Invalid column name for ALTER TABLE: {col!r}")
    if ctype.upper() not in _ALLOWED_COL_TYPES:
        raise ValueError(f"Invalid column type for ALTER TABLE: {ctype!r}")

KST = timezone(timedelta(hours=9))

def now_kst():
    return datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')

logger = logging.getLogger(__name__)


class DB:
    BATCH_SIZE = 100       # commit every N writes
    BATCH_INTERVAL = 2.0   # or every N seconds, whichever comes first

    def __init__(self, db_path: str = "web_server_prod/rtu_dashboard.db"):
        self.db_path = db_path
        self.db: aiosqlite.Connection | None = None
        self._pending_writes = 0
        self._last_commit = 0.0
        self._commit_task: asyncio.Task | None = None

    async def init_db(self):
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row

        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA synchronous=NORMAL")
        await self.db.execute("PRAGMA busy_timeout=5000")

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS rtu_registry (
                rtu_id INTEGER PRIMARY KEY,
                ip TEXT, port INTEGER,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                last_seen TEXT, status TEXT DEFAULT 'online')""")

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS inverter_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                rtu_id INTEGER, device_number INTEGER, model INTEGER,
                pv_voltage REAL, pv_current REAL, pv_power REAL,
                ac_power REAL, power_factor REAL, frequency REAL,
                cumulative_energy REAL, status INTEGER,
                r_voltage REAL, s_voltage REAL, t_voltage REAL,
                r_current REAL, s_current REAL, t_current REAL,
                raw_hex TEXT,
                backup INTEGER DEFAULT 0, original_timestamp TEXT)""")

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS relay_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                rtu_id INTEGER, device_number INTEGER,
                r_voltage REAL, s_voltage REAL, t_voltage REAL,
                r_current REAL, s_current REAL, t_current REAL,
                total_active_power REAL, avg_power_factor REAL, frequency REAL,
                received_energy REAL, sent_energy REAL,
                do_status INTEGER, di_status INTEGER,
                inverter_power REAL, load_power REAL,
                backup INTEGER DEFAULT 0, original_timestamp TEXT)""")
        # Migrate: add columns if missing (existing DB)
        for col, typ in [('received_energy','REAL'), ('sent_energy','REAL'),
                         ('do_status','INTEGER'), ('di_status','INTEGER'),
                         ('inverter_power','REAL'), ('load_power','REAL'),
                         ('total_active_power','REAL'), ('avg_power_factor','REAL')]:
            try:
                _validate_sql_col(col, typ)
                await self.db.execute(f"ALTER TABLE relay_data ADD COLUMN {col} {typ} DEFAULT 0")
            except ValueError as e:
                logger.error(f"ALTER TABLE validation failed: {e}")
            except Exception:
                pass

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS weather_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                rtu_id INTEGER, device_number INTEGER,
                air_temp REAL, air_humidity REAL, air_pressure REAL,
                wind_speed REAL, wind_direction REAL,
                module_temp_1 REAL, module_temp_2 REAL,
                module_temp_3 REAL, module_temp_4 REAL,
                horizontal_radiation REAL, horizontal_accum REAL,
                inclined_radiation REAL, inclined_accum REAL,
                backup INTEGER DEFAULT 0, original_timestamp TEXT)""")

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                rtu_id INTEGER, event_type TEXT,
                body_type INTEGER, detail TEXT)""")

        # Safe column additions for existing DBs
        for table in ('inverter_data', 'relay_data'):
            for col, ctype, default in [('backup', 'INTEGER', '0'), ('original_timestamp', 'TEXT', 'NULL')]:
                try:
                    _validate_sql_col(col, ctype)
                    await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ctype} DEFAULT {default}")
                except ValueError as e:
                    logger.error(f"ALTER TABLE validation failed: {e}")
                except Exception:
                    pass
        try:
            await self.db.execute("ALTER TABLE event_log ADD COLUMN body_type INTEGER DEFAULT 0")
        except Exception:
            pass
        # Rename columns for field name standardization (existing DBs)
        for rename_sql in [
            "ALTER TABLE relay_data RENAME COLUMN total_power TO total_active_power",
            "ALTER TABLE relay_data RENAME COLUMN power_factor TO avg_power_factor",
            "ALTER TABLE weather_data RENAME COLUMN humidity TO air_humidity",
        ]:
            try:
                await self.db.execute(rename_sql)
            except Exception:
                pass

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS control_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                rtu_id INTEGER, device_number INTEGER,
                on_off INTEGER, power_factor INTEGER,
                operation_mode INTEGER, reactive_power_pct INTEGER,
                active_power_pct INTEGER)""")

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS control_monitor (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                rtu_id INTEGER, device_number INTEGER,
                current_r REAL, current_s REAL, current_t REAL,
                voltage_rs REAL, voltage_st REAL, voltage_tr REAL,
                active_power_kw REAL, reactive_power_var REAL,
                power_factor REAL, frequency REAL,
                status_flags INTEGER)""")

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS rtu_registry (
                rtu_id INTEGER PRIMARY KEY,
                ip TEXT, port INTEGER, status TEXT DEFAULT 'offline',
                model TEXT, phone TEXT, serial TEXT, firmware TEXT,
                rtu_type TEXT,
                first_seen TEXT, last_seen TEXT, last_info_update TEXT,
                note TEXT)""")
        # Migrate existing rtu_registry tables
        for col, typ in [('model','TEXT'), ('phone','TEXT'), ('serial','TEXT'),
                         ('firmware','TEXT'), ('rtu_type','TEXT'),
                         ('last_info_update','TEXT'), ('note','TEXT'),
                         ('hidden','INTEGER DEFAULT 0')]:
            try:
                _validate_sql_col(col, typ)
                await self.db.execute(f"ALTER TABLE rtu_registry ADD COLUMN {col} {typ}")
            except ValueError as e:
                logger.error(f"ALTER TABLE validation failed: {e}")
            except Exception:
                pass

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS rtu_connection_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                rtu_id INTEGER, event TEXT, ip TEXT, detail TEXT)""")

        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_inv_rtu_ts ON inverter_data(rtu_id, timestamp)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_relay_rtu_ts ON relay_data(rtu_id, timestamp)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_weather_rtu_ts ON weather_data(rtu_id, timestamp)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_event_rtu_ts ON event_log(rtu_id, timestamp)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_ctrl_status_rtu_ts ON control_status(rtu_id, timestamp)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_ctrl_monitor_rtu_ts ON control_monitor(rtu_id, timestamp)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_conn_log_rtu ON rtu_connection_log(rtu_id, timestamp)")

        await self.db.commit()
        self._last_commit = time.time()
        self._commit_task = asyncio.create_task(self._batch_commit_loop())
        logger.info(f"Database initialized: {self.db_path} (batch commit: {self.BATCH_SIZE} rows / {self.BATCH_INTERVAL}s)")

    async def close(self):
        if self._commit_task:
            self._commit_task.cancel()
            try:
                await self._commit_task
            except (asyncio.CancelledError, Exception):
                pass
        if self.db:
            # Final flush
            try:
                await self.db.commit()
            except Exception:
                pass
            await self.db.close()
            self.db = None

    async def _batch_commit_loop(self):
        """Periodic commit for batched writes."""
        while True:
            try:
                await asyncio.sleep(self.BATCH_INTERVAL)
                if self.db and self._pending_writes > 0:
                    await self.db.commit()
                    self._pending_writes = 0
                    self._last_commit = time.time()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Batch commit error: {e}")

    async def _maybe_commit(self):
        """Commit if batch size reached."""
        self._pending_writes += 1
        if self._pending_writes >= self.BATCH_SIZE:
            await self.db.commit()
            self._pending_writes = 0
            self._last_commit = time.time()

    # ----- RTU Registry -----

    async def upsert_rtu(self, rtu_id: int, ip: str, port: int):
        await self.db.execute("""
            INSERT INTO rtu_registry (rtu_id, ip, port, last_seen, status)
            VALUES (?, ?, ?, ?, 'online')
            ON CONFLICT(rtu_id) DO UPDATE SET
                ip=excluded.ip, port=excluded.port, last_seen=?, status='online'
                WHERE hidden IS NOT 1
        """, (rtu_id, ip, port, now_kst(), now_kst()))
        await self._maybe_commit()

    async def get_rtus(self):
        # hidden RTU도 표시하되 status='offline' 강제 — 재접속해도 ONLINE이 되지 않음
        async with self.db.execute(
            "SELECT * FROM rtu_registry ORDER BY rtu_id"
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
        for r in rows:
            if r.get('hidden'):
                r['status'] = 'offline'
        return rows

    async def get_rtu(self, rtu_id: int):
        async with self.db.execute("SELECT * FROM rtu_registry WHERE rtu_id=?", (rtu_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def set_rtu_offline(self, rtu_id: int):
        await self.db.execute("UPDATE rtu_registry SET status='offline', last_seen=? WHERE rtu_id=?", (now_kst(), rtu_id))
        await self.db.commit()
        self._pending_writes = 0

    async def save_rtu_info(self, rtu_id: int, info: dict):
        """UPSERT RTU info from H05 rtu_info event."""
        ts = now_kst()
        model = info.get('model', '')
        rtu_type = 'RIP' if 'SRPV' in model else ('SOLARIZE' if model else '')
        existing = await self.get_rtu(rtu_id)
        if existing:
            if existing.get('hidden'):
                return  # hidden RTU는 업데이트 안 함
            await self.db.execute("""
                UPDATE rtu_registry SET model=?, phone=?, serial=?, firmware=?,
                    rtu_type=?, last_info_update=?, last_seen=?
                WHERE rtu_id=?
            """, (model, info.get('phone',''), info.get('serial',''),
                  info.get('firmware',''), rtu_type, ts, ts, rtu_id))
        else:
            await self.db.execute("""
                INSERT INTO rtu_registry (rtu_id, model, phone, serial, firmware,
                    rtu_type, first_seen, last_seen, last_info_update)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (rtu_id, model, info.get('phone',''), info.get('serial',''),
                  info.get('firmware',''), rtu_type, ts, ts, ts))
        await self.db.commit()

    async def log_rtu_connection(self, rtu_id: int, event: str, ip: str = '', detail: str = ''):
        """Log online/offline event."""
        await self.db.execute(
            "INSERT INTO rtu_connection_log (timestamp, rtu_id, event, ip, detail) VALUES (?,?,?,?,?)",
            (now_kst(), rtu_id, event, ip, detail))
        await self._maybe_commit()

    async def get_rtu_connection_log(self, rtu_id: int = None, limit: int = 100):
        query = "SELECT * FROM rtu_connection_log WHERE 1=1"
        params = []
        if rtu_id is not None:
            query += " AND rtu_id=?"; params.append(rtu_id)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        async with self.db.execute(query, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def delete_rtu(self, rtu_id: int):
        """Hide RTU from dashboard (keeps DB data, prevents re-display on reconnect)."""
        await self.db.execute(
            "UPDATE rtu_registry SET hidden=1, status='offline' WHERE rtu_id=?", (rtu_id,))
        await self.db.execute("DELETE FROM rtu_connection_log WHERE rtu_id=?", (rtu_id,))
        await self.db.commit()

    # ----- Inverter Data -----

    async def save_inverter_data(self, rtu_id: int, parsed: dict):
        is_backup = parsed.get('backup', 0)
        original_ts = parsed.get('original_timestamp')
        ts = original_ts if (is_backup and original_ts) else now_kst()

        await self.db.execute("""
            INSERT INTO inverter_data (
                timestamp, rtu_id, device_number, model,
                pv_voltage, pv_current, pv_power,
                ac_power, power_factor, frequency,
                cumulative_energy, status,
                r_voltage, s_voltage, t_voltage,
                r_current, s_current, t_current,
                raw_hex, backup, original_timestamp
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (ts, rtu_id,
              parsed.get('device_number', 0), parsed.get('model', 0),
              parsed.get('pv_voltage', 0), parsed.get('pv_current', 0), parsed.get('pv_power', 0),
              parsed.get('ac_power', 0), parsed.get('power_factor', 0), parsed.get('frequency', 0),
              parsed.get('cumulative_energy', 0), parsed.get('status', 0),
              parsed.get('r_voltage', 0), parsed.get('s_voltage', 0), parsed.get('t_voltage', 0),
              parsed.get('r_current', 0), parsed.get('s_current', 0), parsed.get('t_current', 0),
              parsed.get('raw_hex', ''), is_backup, original_ts))
        await self._maybe_commit()

    async def get_inverter_history(self, rtu_id=None, device_num=None,
                                   from_ts=None, to_ts=None, limit=100):
        limit = min(limit, 10000)
        query = "SELECT * FROM inverter_data WHERE 1=1"
        params = []
        if rtu_id is not None:
            query += " AND rtu_id=?"; params.append(rtu_id)
        if device_num is not None:
            query += " AND device_number=?"; params.append(device_num)
        if from_ts:
            query += " AND timestamp>=?"; params.append(from_ts)
        if to_ts:
            query += " AND timestamp<=?"; params.append(to_ts)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        async with self.db.execute(query, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    # ----- Relay Data -----

    async def save_relay_data(self, rtu_id: int, parsed: dict):
        is_backup = parsed.get('backup', 0)
        original_ts = parsed.get('original_timestamp')
        ts = original_ts if (is_backup and original_ts) else now_kst()

        await self.db.execute("""
            INSERT INTO relay_data (
                timestamp, rtu_id, device_number,
                r_voltage, s_voltage, t_voltage,
                r_current, s_current, t_current,
                total_active_power, avg_power_factor, frequency,
                received_energy, sent_energy, do_status, di_status,
                inverter_power, load_power,
                backup, original_timestamp
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (ts, rtu_id, parsed.get('device_number', 0),
              parsed.get('r_voltage', 0), parsed.get('s_voltage', 0), parsed.get('t_voltage', 0),
              parsed.get('r_current', 0), parsed.get('s_current', 0), parsed.get('t_current', 0),
              parsed.get('total_active_power', 0), parsed.get('avg_power_factor', 0), parsed.get('frequency', 0),
              parsed.get('received_energy', 0), parsed.get('sent_energy', 0),
              parsed.get('do_status', 0), parsed.get('di_status', 0),
              parsed.get('inverter_power', 0), parsed.get('load_power', 0),
              is_backup, original_ts))
        await self._maybe_commit()

    async def get_relay_history(self, rtu_id=None, device_num=None,
                                from_ts=None, to_ts=None, limit=100):
        limit = min(limit, 10000)
        query = "SELECT * FROM relay_data WHERE 1=1"
        params = []
        if rtu_id is not None:
            query += " AND rtu_id=?"; params.append(rtu_id)
        if device_num is not None:
            query += " AND device_number=?"; params.append(device_num)
        if from_ts:
            query += " AND timestamp>=?"; params.append(from_ts)
        if to_ts:
            query += " AND timestamp<=?"; params.append(to_ts)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        async with self.db.execute(query, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    # ----- Weather Data -----

    async def save_weather_data(self, rtu_id: int, parsed: dict):
        is_backup = parsed.get('backup', 0)
        original_ts = parsed.get('original_timestamp')
        ts = original_ts if (is_backup and original_ts) else now_kst()

        await self.db.execute("""
            INSERT INTO weather_data (
                timestamp, rtu_id, device_number,
                air_temp, air_humidity, air_pressure,
                wind_speed, wind_direction,
                module_temp_1, module_temp_2, module_temp_3, module_temp_4,
                horizontal_radiation, horizontal_accum,
                inclined_radiation, inclined_accum,
                backup, original_timestamp
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (ts, rtu_id, parsed.get('device_number', 0),
              parsed.get('air_temp', 0), parsed.get('air_humidity', 0), parsed.get('air_pressure', 0),
              parsed.get('wind_speed', 0), parsed.get('wind_direction', 0),
              parsed.get('module_temp_1', 0), parsed.get('module_temp_2', 0),
              parsed.get('module_temp_3', 0), parsed.get('module_temp_4', 0),
              parsed.get('horizontal_radiation', 0), parsed.get('horizontal_accum', 0),
              parsed.get('inclined_radiation', 0), parsed.get('inclined_accum', 0),
              is_backup, original_ts))
        await self._maybe_commit()

    async def get_weather_history(self, rtu_id=None, device_num=None,
                                  from_ts=None, to_ts=None, limit=100):
        limit = min(limit, 10000)
        query = "SELECT * FROM weather_data WHERE 1=1"
        params = []
        if rtu_id is not None:
            query += " AND rtu_id=?"; params.append(rtu_id)
        if device_num is not None:
            query += " AND device_number=?"; params.append(device_num)
        if from_ts:
            query += " AND timestamp>=?"; params.append(from_ts)
        if to_ts:
            query += " AND timestamp<=?"; params.append(to_ts)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        async with self.db.execute(query, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    # ----- Control Status / Monitor -----

    async def save_control_status(self, rtu_id: int, device_number: int, data: dict):
        await self.db.execute("""
            INSERT INTO control_status (
                timestamp, rtu_id, device_number,
                on_off, power_factor, operation_mode,
                reactive_power_pct, active_power_pct
            ) VALUES (?,?,?,?,?,?,?,?)
        """, (now_kst(), rtu_id, device_number,
              data.get('on_off', 0), data.get('power_factor', 1000),
              data.get('operation_mode', 0),
              data.get('reactive_power_pct', 0), data.get('active_power_pct', 1000)))
        await self._maybe_commit()

    async def save_control_monitor(self, rtu_id: int, device_number: int, data: dict):
        await self.db.execute("""
            INSERT INTO control_monitor (
                timestamp, rtu_id, device_number,
                current_r, current_s, current_t,
                voltage_rs, voltage_st, voltage_tr,
                active_power_kw, reactive_power_var,
                power_factor, frequency, status_flags
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (now_kst(), rtu_id, device_number,
              data.get('current_r', 0), data.get('current_s', 0), data.get('current_t', 0),
              data.get('voltage_rs', 0), data.get('voltage_st', 0), data.get('voltage_tr', 0),
              data.get('active_power_kw', 0), data.get('reactive_power_var', 0),
              data.get('power_factor', 0), data.get('frequency', 0),
              data.get('status_flags', 0)))
        await self._maybe_commit()

    async def get_latest_control_status(self, rtu_id: int, device_number: int):
        async with self.db.execute("""
            SELECT * FROM control_status
            WHERE rtu_id=? AND device_number=?
            ORDER BY id DESC LIMIT 1
        """, (rtu_id, device_number)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_latest_control_monitor(self, rtu_id: int, device_number: int):
        async with self.db.execute("""
            SELECT * FROM control_monitor
            WHERE rtu_id=? AND device_number=?
            ORDER BY id DESC LIMIT 1
        """, (rtu_id, device_number)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ----- Event Log -----

    async def save_event(self, rtu_id: int, event_type: str, detail: str, body_type: int = 0):
        await self.db.execute(
            "INSERT INTO event_log (timestamp, rtu_id, event_type, body_type, detail) VALUES (?,?,?,?,?)",
            (now_kst(), rtu_id, event_type, body_type, detail))
        await self._maybe_commit()

    async def get_events(self, rtu_id=None, limit=100, offset=0, from_ts=None):
        limit = min(limit, 10000)
        query = "SELECT * FROM event_log WHERE 1=1"
        params = []
        if rtu_id is not None:
            query += " AND rtu_id=?"; params.append(rtu_id)
        if from_ts:
            query += " AND timestamp>=?"; params.append(from_ts)
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with self.db.execute(query, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    # ----- Production: Maintenance -----

    async def cleanup_old_data(self, retention_days: int = 30) -> int:
        """Delete raw data older than retention_days (already aggregated by downsampler)."""
        cutoff = (datetime.now(KST) - timedelta(days=retention_days)).strftime('%Y-%m-%d %H:%M:%S')
        total = 0
        for table in ('inverter_data', 'relay_data', 'event_log', 'control_status', 'control_monitor'):
            cursor = await self.db.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))
            total += cursor.rowcount
        await self.db.commit()
        self._pending_writes = 0
        return total

    async def downsample_data(self) -> dict:
        """Tiered data retention:
        - 30d~1y: downsample to 5-minute averages
        - 1y+: downsample to 1-hour averages
        Each step runs in its own transaction (atomic delete+insert).
        """
        now = datetime.now(KST)
        cutoff_30d = (now - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        cutoff_1y = (now - timedelta(days=365)).strftime('%Y-%m-%d %H:%M:%S')
        result = {'5min_inserted': 0, '1h_inserted': 0}

        # Each downsample step is independently committed
        result['5min_inserted'] += await self._downsample_inverter(cutoff_1y, cutoff_30d, 5)
        result['5min_inserted'] += await self._downsample_relay(cutoff_1y, cutoff_30d, 5)
        result['5min_inserted'] += await self._downsample_weather(cutoff_1y, cutoff_30d, 5)
        result['1h_inserted'] += await self._downsample_inverter(None, cutoff_1y, 60)
        result['1h_inserted'] += await self._downsample_relay(None, cutoff_1y, 60)
        result['1h_inserted'] += await self._downsample_weather(None, cutoff_1y, 60)

        return result

    async def _downsample_inverter(self, ts_from, ts_to, interval_min: int) -> int:
        """Atomically aggregate inverter_data into interval_min-minute buckets."""
        where = "WHERE timestamp < ?"
        params = [ts_to]
        if ts_from:
            where = "WHERE timestamp >= ? AND timestamp < ?"
            params = [ts_from, ts_to]

        # BEGIN IMMEDIATE 먼저 → SELECT 집계 → DELETE → INSERT → COMMIT
        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute(f"SELECT COUNT(*) FROM inverter_data {where}", params) as cur:
                count = (await cur.fetchone())[0]
            if count == 0:
                await self.db.execute("ROLLBACK")
                return 0

            bucket = f"strftime('%Y-%m-%d %H:', timestamp) || printf('%02d', (CAST(strftime('%M', timestamp) AS INTEGER) / {interval_min}) * {interval_min}) || ':00'"
            agg_q = f"""
                SELECT {bucket} as ts_bucket, rtu_id, device_number, model,
                       AVG(pv_voltage), AVG(pv_current), AVG(pv_power),
                       AVG(r_voltage), AVG(s_voltage), AVG(t_voltage),
                       AVG(r_current), AVG(s_current), AVG(t_current),
                       AVG(ac_power), AVG(power_factor), AVG(frequency),
                       MAX(cumulative_energy), MAX(status), 0, NULL
                FROM inverter_data {where}
                GROUP BY ts_bucket, rtu_id, device_number
            """
            async with self.db.execute(agg_q, params) as cur:
                rows = await cur.fetchall()
            if not rows:
                await self.db.execute("ROLLBACK")
                return 0
        except Exception as e:
            await self.db.execute("ROLLBACK")
            raise

        # Atomic: DELETE → INSERT all → COMMIT (already in IMMEDIATE)
        try:
            await self.db.execute(f"DELETE FROM inverter_data {where}", params)
            await self.db.executemany("""
                INSERT INTO inverter_data
                (timestamp, rtu_id, device_number, model,
                 pv_voltage, pv_current, pv_power,
                 r_voltage, s_voltage, t_voltage,
                 r_current, s_current, t_current,
                 ac_power, power_factor, frequency,
                 cumulative_energy, status, backup, original_timestamp)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, [tuple(r) for r in rows])
            await self.db.commit()
        except Exception as e:
            logger.error(f"Downsample inverter error: {e}")
            try:
                await self.db.rollback()
            except Exception as rb_err:
                logger.error(f"Rollback failed: {rb_err}")
            raise
        return len(rows)

    async def _downsample_relay(self, ts_from, ts_to, interval_min: int) -> int:
        """Atomically aggregate relay_data into interval_min-minute buckets."""
        where = "WHERE timestamp < ?"
        params = [ts_to]
        if ts_from:
            where = "WHERE timestamp >= ? AND timestamp < ?"
            params = [ts_from, ts_to]

        # BEGIN IMMEDIATE 먼저 → SELECT 집계 → DELETE → INSERT → COMMIT
        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute(f"SELECT COUNT(*) FROM relay_data {where}", params) as cur:
                count = (await cur.fetchone())[0]
            if count == 0:
                await self.db.execute("ROLLBACK")
                return 0

            bucket = f"strftime('%Y-%m-%d %H:', timestamp) || printf('%02d', (CAST(strftime('%M', timestamp) AS INTEGER) / {interval_min}) * {interval_min}) || ':00'"
            agg_q = f"""
                SELECT {bucket} as ts_bucket, rtu_id, device_number,
                       AVG(r_voltage), AVG(s_voltage), AVG(t_voltage),
                       AVG(r_current), AVG(s_current), AVG(t_current),
                       AVG(total_power), AVG(power_factor), AVG(frequency), 0, NULL
                FROM relay_data {where}
                GROUP BY ts_bucket, rtu_id, device_number
            """
            async with self.db.execute(agg_q, params) as cur:
                rows = await cur.fetchall()
            if not rows:
                await self.db.execute("ROLLBACK")
                return 0
        except Exception as e:
            await self.db.execute("ROLLBACK")
            raise

        # DELETE → INSERT → COMMIT (already in IMMEDIATE)
        try:
            await self.db.execute(f"DELETE FROM relay_data {where}", params)
            for r in rows:
                await self.db.execute("""
                    INSERT INTO relay_data
                    (timestamp, rtu_id, device_number,
                     r_voltage, s_voltage, t_voltage,
                     r_current, s_current, t_current,
                     total_power, power_factor, frequency,
                     backup, original_timestamp)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, tuple(r))
            await self.db.commit()
        except Exception as e:
            logger.error(f"Downsample relay error: {e}")
            try:
                await self.db.rollback()
            except Exception as rb_err:
                logger.error(f"Rollback failed: {rb_err}")
            raise
        return len(rows)

    async def _downsample_weather(self, ts_from, ts_to, interval_min: int) -> int:
        """Atomically aggregate weather_data into interval_min-minute buckets."""
        where = "WHERE timestamp < ?"
        params = [ts_to]
        if ts_from:
            where = "WHERE timestamp >= ? AND timestamp < ?"
            params = [ts_from, ts_to]

        # BEGIN IMMEDIATE 먼저 → SELECT 집계 → DELETE → INSERT → COMMIT
        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute(f"SELECT COUNT(*) FROM weather_data {where}", params) as cur:
                count = (await cur.fetchone())[0]
            if count == 0:
                await self.db.execute("ROLLBACK")
                return 0

            bucket = f"strftime('%Y-%m-%d %H:', timestamp) || printf('%02d', (CAST(strftime('%M', timestamp) AS INTEGER) / {interval_min}) * {interval_min}) || ':00'"
            agg_q = f"""
                SELECT {bucket} as ts_bucket, rtu_id, device_number,
                       AVG(air_temp), AVG(humidity), AVG(air_pressure),
                       AVG(wind_speed), AVG(wind_direction),
                       AVG(module_temp_1), AVG(module_temp_2),
                       AVG(module_temp_3), AVG(module_temp_4),
                       AVG(horizontal_radiation), AVG(horizontal_accum),
                       AVG(inclined_radiation), AVG(inclined_accum), 0, NULL
                FROM weather_data {where}
                GROUP BY ts_bucket, rtu_id, device_number
            """
            async with self.db.execute(agg_q, params) as cur:
                rows = await cur.fetchall()
            if not rows:
                await self.db.execute("ROLLBACK")
                return 0
        except Exception as e:
            await self.db.execute("ROLLBACK")
            raise

        # DELETE → INSERT → COMMIT (already in IMMEDIATE)
        try:
            await self.db.execute(f"DELETE FROM weather_data {where}", params)
            for r in rows:
                await self.db.execute("""
                    INSERT INTO weather_data
                    (timestamp, rtu_id, device_number,
                     air_temp, humidity, air_pressure,
                     wind_speed, wind_direction,
                     module_temp_1, module_temp_2, module_temp_3, module_temp_4,
                     horizontal_radiation, horizontal_accum,
                     inclined_radiation, inclined_accum,
                     backup, original_timestamp)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, tuple(r))
            await self.db.commit()
        except Exception as e:
            logger.error(f"Downsample weather error: {e}")
            try:
                await self.db.rollback()
            except Exception as rb_err:
                logger.error(f"Rollback failed: {rb_err}")
            raise
        return len(rows)

    async def checkpoint_wal(self):
        try:
            await self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            await self.db.execute("PRAGMA optimize")
        except Exception as e:
            logger.debug(f"WAL checkpoint skipped (busy): {e}")
