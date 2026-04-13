"""
RTU Dashboard - Production FastAPI Application Entry Point
Starts the web server, UDP engine, database, and WebSocket manager.

Production improvements over dev version:
- Environment variable configuration
- Graceful shutdown with task draining
- Background tasks for stale RTU detection, data retention, WAL cleanup
- Health endpoint
- Proper H05 event handler
"""

import sys
import os
import asyncio
import logging
import time

# Ensure project root is on sys.path so 'from common.protocol_constants import *' works
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from common.protocol_constants import DEVICE_INVERTER, DEVICE_PROTECTION_RELAY, DEVICE_WEATHER_STATION, CTRL_INV_CONTROL_CHECK

from web_server_prod.udp_engine import UDPEngine
from web_server_prod.db import DB
from web_server_prod.ws_manager import WSManager
from web_server_prod import api_routes

# ---------------------------------------------------------------------------
# Environment Variable Configuration
# ---------------------------------------------------------------------------
UDP_PORT = int(os.environ.get('RTU_UDP_PORT', '13132'))
WEB_PORT = int(os.environ.get('RTU_WEB_PORT', '8080'))
DB_PATH = os.environ.get('RTU_DB_PATH', 'web_server_prod/rtu_dashboard.db')
FTP_USER = os.environ.get('RTU_FTP_USER', 'rtu')
FTP_PASS = os.environ.get('RTU_FTP_PASS', '1234')

# ---------------------------------------------------------------------------
# FTP server for firmware updates
# ---------------------------------------------------------------------------
import socket as _sock


def _get_local_ip():
    try:
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


ftp_manager = None
try:
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
    import threading
    FTP_AVAILABLE = True
except ImportError:
    FTP_AVAILABLE = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rtu_dashboard")

# ---------------------------------------------------------------------------
# Application Components
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup()
    yield
    await shutdown()


app = FastAPI(title="RTU Dashboard", version="1.2.0", lifespan=lifespan)

engine = UDPEngine(listen_port=UDP_PORT)
database = DB(db_path=DB_PATH)
ws_manager = WSManager()

# Wire references into api_routes module
api_routes.engine = engine
api_routes.database = database
api_routes.ws = ws_manager

# Include REST routes
app.include_router(api_routes.router)

# ---------------------------------------------------------------------------
# Static Files & Index
# ---------------------------------------------------------------------------
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.middleware("http")
async def no_cache_static(request, call_next):
    """Prevent browser caching of JS/CSS files during development."""
    response = await call_next(request)
    if request.url.path.endswith(('.js', '.css')):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/favicon.ico")
async def favicon():
    favicon_path = os.path.join(static_dir, "favicon.svg")
    if os.path.isfile(favicon_path):
        return FileResponse(favicon_path, media_type="image/svg+xml")
    return JSONResponse(status_code=404, content={"detail": "favicon not found"})


@app.get("/")
async def index():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {"message": "RTU Dashboard API is running. No static/index.html found."}


# ---------------------------------------------------------------------------
# Health Endpoint
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    rtu_count = len(engine.rtu_registry) if engine else 0
    return JSONResponse({
        "status": "ok",
        "udp_port": UDP_PORT,
        "connected_rtus": rtu_count,
        "uptime_seconds": round(time.time() - _start_time, 1) if _start_time else 0,
    })

_start_time: float = 0.0

# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive
    except Exception:
        ws_manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Async Handlers (bridged from threaded UDP engine)
# ---------------------------------------------------------------------------
async def _handle_h01_async(rtu_id: int, device_key: tuple, parsed: dict):
    """Save H01 data to DB and broadcast via WebSocket."""
    dev_type, dev_num = device_key

    # Skip hidden (deleted) RTUs entirely — no DB update, no WS broadcast
    _rtu_check = await database.get_rtu(rtu_id)
    if _rtu_check and _rtu_check.get('hidden'):
        with engine._lock:
            engine.rtu_registry.pop(rtu_id, None)  # remove from memory too
        return

    # Upsert RTU in database + detect online recovery
    rtu_state = engine.rtu_registry.get(rtu_id)
    if rtu_state:
        # Check if RTU was offline (reconnected without H05 restart)
        rtu_db = await database.get_rtu(rtu_id)
        if rtu_db and rtu_db.get('status') == 'offline':
            await database.save_event(rtu_id, "rtu_reconnect",
                f"RTU resumed from {rtu_state.ip}:{rtu_state.port}")
            await ws_manager.broadcast({
                'type': 'event', 'rtu_id': rtu_id,
                'event_type': 'rtu_reconnect',
                'detail': f"RTU resumed from {rtu_state.ip}:{rtu_state.port}",
            })
            logger.info(f"RTU {rtu_id} reconnected (was offline)")
        await database.upsert_rtu(rtu_id, rtu_state.ip, rtu_state.port)

    # Log backup recovery
    backup_flag = parsed.get('backup', 0)
    if backup_flag == 1:
        await database.save_event(rtu_id, "backup_recovery",
            f"Backup data received: dev {device_key[0]}/{device_key[1]}")
        await ws_manager.broadcast({
            'type': 'event', 'rtu_id': rtu_id,
            'event_type': 'backup_recovery',
            'detail': f"Backup data received: dev {device_key[0]}/{device_key[1]}",
        })

    # Clear comm_fail state when valid data arrives for this device
    if backup_flag == 0:
        fail_key = (rtu_id, f"Device {dev_type}/{dev_num}: COMM_FAIL")
        if _comm_fail_state.get(fail_key):
            _comm_fail_state[fail_key] = False
            await database.save_event(rtu_id, "comm_restored",
                f"Device {dev_type}/{dev_num}: Communication restored")
            await ws_manager.broadcast({
                'type': 'event', 'rtu_id': rtu_id,
                'event_type': 'comm_restored',
                'detail': f"Device {dev_type}/{dev_num}: Communication restored",
            })

    if dev_type == DEVICE_INVERTER:
        await database.save_inverter_data(rtu_id, parsed)
    elif dev_type == DEVICE_PROTECTION_RELAY:
        # Calculate inverter total AC power for PCC power flow
        inv_total_w = 0.0
        rtu_info = engine.rtu_registry.get(rtu_id)
        if rtu_info:
            with engine._lock:
                for dkey, ddata in rtu_info.devices.items():
                    if isinstance(dkey, tuple) and dkey[0] == DEVICE_INVERTER and not ddata.get('error'):
                        inv_total_w += ddata.get('ac_power', 0)
        parsed['inverter_power'] = inv_total_w
        net_power = parsed.get('total_power', 0)
        parsed['load_power'] = net_power + inv_total_w  # Load = Grid + Inverter
        await database.save_relay_data(rtu_id, parsed)
    elif dev_type == DEVICE_WEATHER_STATION:
        await database.save_weather_data(rtu_id, parsed)

    await ws_manager.broadcast({
        'type': 'h01_data',
        'rtu_id': rtu_id,
        'device_type': dev_type,
        'device_number': dev_num,
        'data': parsed,
    })


# High-frequency events: log only, skip DB to prevent bloat
_LOG_ONLY_EVENTS = {"duplicate_dropped", "rate_limited", "heartbeat", "backup_recovery"}
# Events that should broadcast to WebSocket but NOT save to event_log DB
_WS_ONLY_EVENTS = {"h04_response", "H03_SENT", "rtu_info", "iv_scan_data"}

# Track comm_fail state per device: {(rtu_id, detail): True/False}
# True = currently in comm_fail state, suppress duplicate logs
# Max 500 entries to prevent unbounded growth
_comm_fail_state: dict[tuple, bool] = {}
_COMM_FAIL_MAX = 500


_PERIODIC_CONTROL_EVENTS = {"control_check", "control_result"}

async def _handle_event_async(rtu_id: int, event_type: str, detail: str):
    """Save event to DB and broadcast via WebSocket."""
    # Skip hidden (deleted) RTUs
    _rtu_chk = await database.get_rtu(rtu_id)
    if _rtu_chk and _rtu_chk.get('hidden'):
        return

    if event_type in _LOG_ONLY_EVENTS:
        logger.debug(f"[{event_type}] RTU:{rtu_id} {detail}")
        return

    # Suppress periodic status check responses from event_log and WebSocket
    # (data is already saved to control_status/control_monitor tables via dedicated callbacks)
    if event_type in _PERIODIC_CONTROL_EVENTS:
        # Extract device number from detail: "INV{N}: ..."
        try:
            dev_num = int(detail.split(':')[0].replace('INV', ''))
        except (ValueError, IndexError):
            dev_num = 0
        if _is_periodic_response(rtu_id, dev_num):
            return

    # Deduplicate comm_fail: log first occurrence only, skip repeats
    if event_type == "comm_fail":
        key = (rtu_id, detail)
        if _comm_fail_state.get(key):
            return  # already in comm_fail, suppress duplicate
        # Evict oldest entries if over limit
        if len(_comm_fail_state) >= _COMM_FAIL_MAX:
            oldest = next(iter(_comm_fail_state))
            del _comm_fail_state[oldest]
        _comm_fail_state[key] = True

    # Deduplicate nighttime_standby: suppress periodic repeats (same as comm_fail)
    if event_type == "nighttime_standby":
        key = (rtu_id, detail)
        if _comm_fail_state.get(key):
            return
        if len(_comm_fail_state) >= _COMM_FAIL_MAX:
            oldest = next(iter(_comm_fail_state))
            del _comm_fail_state[oldest]
        _comm_fail_state[key] = True

    # Log RTU online event
    if event_type == "rtu_event" and "PORT OPEN" in detail:
        rtu_state = engine.rtu_registry.get(rtu_id)
        ip = f"{rtu_state.ip}:{rtu_state.port}" if rtu_state else ''
        await database.log_rtu_connection(rtu_id, 'online', ip, detail)
        # Ensure RTU exists in registry DB
        existing = await database.get_rtu(rtu_id)
        if not existing:
            await database.save_rtu_info(rtu_id, {})

    # Save RTU info to DB when received
    if event_type == "rtu_info":
        info_dict = {}
        for part in detail.split(', '):
            k, _, v = part.partition('=')
            if k and v:
                info_dict[k.strip()] = v.strip()
        await database.save_rtu_info(rtu_id, info_dict)

    # WS-only events: broadcast but don't save to DB (Control tab only)
    if event_type not in _WS_ONLY_EVENTS:
        await database.save_event(rtu_id, event_type, detail)

    await ws_manager.broadcast({
        'type': 'event',
        'rtu_id': rtu_id,
        'event_type': event_type,
        'detail': detail,
    })


async def _handle_h05_async(rtu_id: int, parsed: dict):
    """Broadcast H05 event via WebSocket only (DB save handled by on_event path)."""
    event_type = parsed.get('event_type', 'unknown')
    detail = parsed.get('detail', '')
    body_type = parsed.get('body_type', 0)

    if event_type in _LOG_ONLY_EVENTS or event_type in _WS_ONLY_EVENTS:
        return  # already broadcast via on_event path

    await ws_manager.broadcast({
        'type': 'h05_event',
        'rtu_id': rtu_id,
        'event_type': event_type,
        'detail': detail,
        'device_number': parsed.get('device_number', 0),
        'body_type': body_type,
    })


async def _handle_raw_packet_async(rtu_id: int, info: dict):
    """Broadcast raw H1 packet info to WebSocket for H1 Log tab."""
    await ws_manager.broadcast({
        'type': 'raw_packet',
        'rtu_id': rtu_id,
        **info,
    })


# ---------------------------------------------------------------------------
# Background Tasks
# ---------------------------------------------------------------------------
_background_tasks: list[asyncio.Task] = []


async def _stale_rtu_checker():
    """Every 60s: detect stale RTUs and mark offline in DB.
    Threshold is per-RTU: max(observed_interval * 2, 120s), capped at 660s.
    This handles RTUs with different H01 periods (1min, 5min, etc.)."""
    _disk_check_count = 0
    while True:
        try:
            await asyncio.sleep(60)
            now = time.time()

            # Disk space check every 10 minutes
            _disk_check_count += 1
            if _disk_check_count % 10 == 0:
                try:
                    import shutil
                    usage = shutil.disk_usage('.')
                    free_gb = usage.free / (1024 ** 3)
                    if free_gb < 1.0:
                        logger.warning(f"Low disk space: {free_gb:.1f}GB free")
                        await database.save_event(
                            0, "disk_warning", f"Low disk: {free_gb:.1f}GB free")
                except Exception:
                    pass

            stale_ids = []
            remove_ids = []
            with engine._lock:
                for rtu_id, state in list(engine.rtu_registry.items()):
                    age = now - state.last_seen
                    # Per-RTU threshold: 2x observed interval, min 120s, max 660s
                    interval = getattr(state, 'avg_interval', 0)
                    threshold = max(interval * 2, 120) if interval > 0 else 360
                    threshold = min(threshold, 660)  # cap at 11 minutes

                    if age > threshold and state.connected:
                        stale_ids.append((rtu_id, threshold))
                        state.connected = False
                    # Remove from memory after 2 hours offline (prevent unbounded growth)
                    elif age > 7200 and not state.connected:
                        remove_ids.append(rtu_id)

                for rtu_id in remove_ids:
                    del engine.rtu_registry[rtu_id]

            for rtu_id, threshold in stale_ids:
                await database.set_rtu_offline(rtu_id)
                await database.log_rtu_connection(rtu_id, 'offline', '', f"No data for {int(threshold)}s")
                await database.save_event(rtu_id, "rtu_offline",
                    f"No data for {int(threshold)}s")
                logger.info(f"RTU {rtu_id} marked offline (stale > {int(threshold)}s)")
                await ws_manager.broadcast({
                    'type': 'rtu_offline',
                    'rtu_id': rtu_id,
                })
            if remove_ids:
                logger.info(f"Removed {len(remove_ids)} stale RTUs from memory: {remove_ids}")

            # Also clean stale IV scan data
            engine.cleanup_stale_iv_scans()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Stale RTU checker error: {e}")


async def _data_retention_task():
    """Every 1h: clean up data older than 30 days."""
    while True:
        try:
            await asyncio.sleep(3600)
            deleted = await database.cleanup_old_data(30)
            if deleted > 0:
                logger.info(f"Data retention cleanup: removed {deleted} old records")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Data retention task error: {e}")


async def _wal_checkpoint_task():
    """Every 1h: WAL checkpoint for SQLite."""
    while True:
        try:
            await asyncio.sleep(3600)
            await database.checkpoint_wal()
            logger.info("WAL checkpoint completed")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"WAL checkpoint error: {e}")


async def _downsample_task():
    """Daily at 3AM KST: downsample old data.
    - 30d~1y: 5-minute averages
    - 1y+: 1-hour averages
    """
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    while True:
        try:
            # Calculate seconds until next 3AM KST
            now = datetime.now(KST)
            next_3am = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if now >= next_3am:
                next_3am += timedelta(days=1)
            wait_secs = (next_3am - now).total_seconds()
            logger.info(f"Downsample scheduled in {wait_secs/3600:.1f}h (next 3AM KST)")
            await asyncio.sleep(wait_secs)

            result = await database.downsample_data()
            total = result['5min_inserted'] + result['1h_inserted']
            if total > 0:
                logger.info(f"Downsample complete: 5min={result['5min_inserted']}, 1h={result['1h_inserted']}")
            else:
                logger.info("Downsample: no data to aggregate")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Downsample error: {e}, retrying in 10 min")
            await asyncio.sleep(600)  # Retry in 10min, then recalculate next 3AM


# Dual timestamp tracking: periodic vs manual
# Key: (rtu_id, dev_num), Value: timestamp
_periodic_check_ts: dict[tuple, float] = {}   # when last periodic check was sent
_manual_command_ts: dict[tuple, float] = {}    # when last manual command was sent

def _is_periodic_response(rtu_id: int, dev_num: int) -> bool:
    """Determine if a control response should be suppressed (periodic) or shown (manual).

    Rule: If a manual command was sent within the last 30 seconds, ALWAYS show.
    Only suppress if no manual command is active AND periodic check is recent.
    """
    key = (rtu_id, dev_num)
    manual_ts = _manual_command_ts.get(key, 0)
    periodic_ts = _periodic_check_ts.get(key, 0)
    now = time.time()

    # Manual command sent within 30 seconds → ALWAYS show (never suppress)
    if manual_ts > 0 and (now - manual_ts) < 30:
        return False

    # No recent manual command, but periodic check was recent → suppress
    if periodic_ts > 0 and (now - periodic_ts) < 30:
        return True

    # No recent activity → default to show
    return False

def mark_manual_command(rtu_id: int, dev_num: int):
    """Called by api_routes when a manual H03 command is sent."""
    _manual_command_ts[(rtu_id, dev_num)] = time.time()

def send_control_check_for_rtu(rtu_id: int):
    """Called by udp_engine after H01 cycle complete — send H03(13) to each inverter."""
    with engine._lock:
        state = engine.rtu_registry.get(rtu_id)
    if not state or not state.connected:
        return
    for dk in list(state.devices.keys()):
        dev_type, dev_num = dk
        if dev_type == DEVICE_INVERTER:
            _periodic_check_ts[(rtu_id, dev_num)] = time.time()
            engine.send_h03(rtu_id, CTRL_INV_CONTROL_CHECK, dev_type, dev_num, 0)
    # Cleanup stale entries (older than 120s)
    now = time.time()
    for d in (_periodic_check_ts, _manual_command_ts):
        stale = [k for k, v in d.items() if now - v > 120]
        for k in stale:
            del d[k]


# ---------------------------------------------------------------------------
# Lifecycle Events
# ---------------------------------------------------------------------------
async def startup():
    global _start_time
    _start_time = time.time()

    await database.init_db()
    logger.info("Database initialized")

    # Clear old events on startup, keep last 7 days for diagnostics
    await database.db.execute(
        "DELETE FROM event_log WHERE timestamp < datetime('now', '-7 days')")
    await database.db.commit()
    await database.save_event(0, "server_start", "Dashboard server started")
    logger.info("Event log cleared on startup")

    # Restore RTU info from DB into engine RTUState (survives server restart)
    db_rtus = await database.get_rtus()
    for rtu_db in db_rtus:
        rid = rtu_db.get('rtu_id')
        if rid and rtu_db.get('model'):
            from web_server_prod.udp_engine import RTUState
            if rid not in engine.rtu_registry:
                engine.rtu_registry[rid] = RTUState(rtu_id=rid)
            engine.rtu_registry[rid].rtu_info = {
                'model': rtu_db.get('model', ''),
                'phone': rtu_db.get('phone', ''),
                'serial': rtu_db.get('serial', ''),
                'firmware': rtu_db.get('firmware', ''),
            }
    if db_rtus:
        logger.info(f"Restored {len(db_rtus)} RTU(s) from DB")

    # Get the running event loop for bridging threaded callbacks to async
    loop = asyncio.get_event_loop()

    def on_h01(rtu_id, device_key, parsed):
        asyncio.run_coroutine_threadsafe(
            _handle_h01_async(rtu_id, device_key, parsed), loop
        )

    def on_event(rtu_id, event_type, detail):
        asyncio.run_coroutine_threadsafe(
            _handle_event_async(rtu_id, event_type, detail), loop
        )

    def on_h04(rtu_id, parsed):
        # Suppress periodic status check H04 responses
        dev_num = parsed.get('device_number', 0)
        if _is_periodic_response(rtu_id, dev_num):
            return
        asyncio.run_coroutine_threadsafe(
            _handle_event_async(rtu_id, "h04_response", str(parsed)), loop
        )

    def on_h05(rtu_id, parsed):
        asyncio.run_coroutine_threadsafe(
            _handle_h05_async(rtu_id, parsed), loop
        )

    def on_raw_packet(rtu_id, info):
        asyncio.run_coroutine_threadsafe(
            _handle_raw_packet_async(rtu_id, info), loop
        )

    def on_control_status(rtu_id, dev_num, data):
        asyncio.run_coroutine_threadsafe(
            database.save_control_status(rtu_id, dev_num, data), loop)

    def on_control_monitor(rtu_id, dev_num, data):
        asyncio.run_coroutine_threadsafe(
            database.save_control_monitor(rtu_id, dev_num, data), loop)

    engine.on_h01_data = on_h01
    engine.on_event = on_event
    engine.on_h04 = on_h04
    engine.on_h05 = on_h05
    engine.on_raw_packet = on_raw_packet
    engine.on_control_status = on_control_status
    engine.on_control_monitor = on_control_monitor

    engine.start()
    logger.info(f"UDP engine started on port {UDP_PORT}")

    # Start background tasks
    _background_tasks.append(asyncio.create_task(_stale_rtu_checker()))
    _background_tasks.append(asyncio.create_task(_data_retention_task()))
    _background_tasks.append(asyncio.create_task(_wal_checkpoint_task()))
    _background_tasks.append(asyncio.create_task(_downsample_task()))
    engine.on_h01_cycle_complete = send_control_check_for_rtu
    logger.info("Background tasks started (stale checker, data retention, WAL checkpoint, downsample)")

    # Start built-in FTP server for firmware updates
    global ftp_manager
    if FTP_AVAILABLE:
        firmware_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                     "pc_programs", "firmware")
        os.makedirs(firmware_dir, exist_ok=True)
        try:
            authorizer = DummyAuthorizer()
            authorizer.add_user(FTP_USER, FTP_PASS, firmware_dir, perm="elradfmw")
            handler = FTPHandler
            handler.authorizer = authorizer
            handler.passive_ports = range(60000, 60100)
            ftp_srv = FTPServer(("0.0.0.0", 21), handler)
            ftp_srv.max_cons = 10
            t = threading.Thread(target=ftp_srv.serve_forever, daemon=True)
            t.start()
            ftp_manager = ftp_srv
            local_ip = _get_local_ip()
            api_routes.FTP_LOCAL_IP = local_ip
            logger.info(f"FTP server started on port 21 (root: {firmware_dir}, user: {FTP_USER})")
        except Exception as e:
            logger.warning(f"FTP server failed to start: {e}")
    else:
        logger.warning("pyftpdlib not installed - FTP server disabled")


async def shutdown():
    # Stop UDP engine first (prevents new packets arriving during shutdown)
    engine.stop()

    # Cancel background tasks
    for task in _background_tasks:
        task.cancel()
    if _background_tasks:
        try:
            await asyncio.wait_for(
                asyncio.gather(*_background_tasks, return_exceptions=True),
                timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            logger.warning("Background tasks did not finish in 5s")
    _background_tasks.clear()

    # Wait for pending batch writes to flush (max 3 seconds)
    for _ in range(30):
        if database._pending_writes == 0:
            break
        await asyncio.sleep(0.1)

    await database.close()
    logger.info("RTU Dashboard shut down")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # DB 초기화 옵션 (기본: 유지)
    if os.path.exists(DB_PATH):
        choice = input(f"  기존 DB 발견: {DB_PATH}\n  삭제하고 새로 시작? [y/N]: ").strip().lower()
        if choice == 'y':
            os.remove(DB_PATH)
            # WAL/SHM 파일도 삭제
            for ext in ('-wal', '-shm'):
                p = DB_PATH + ext
                if os.path.exists(p):
                    os.remove(p)
            print("  DB 삭제 완료. 새로 생성됩니다.")
        else:
            print("  기존 DB 유지.")
    print()

    uvicorn.run(
        "web_server_prod.main:app",
        host="0.0.0.0",
        port=WEB_PORT,
        log_level="info",
    )
