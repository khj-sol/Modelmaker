"""
FastAPI REST API Routes for RTU Dashboard — Production Version
Provides endpoints for RTU management, data queries, control commands,
firmware updates, and event logging.

Security improvements over dev version:
- SSH/FTP credentials from environment variables
- SSH command injection prevention (SFTP instead of exec_command for reads)
- Path whitelist for config file access
- Firmware upload validation (extension, size, filename sanitization)
- IV scan filename validation
- Input validation (limit caps, timestamp format)
- Health endpoint
- Config file listing via SFTP listdir()
"""

import os
import re
import time
import logging
import ipaddress
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, Query
from pydantic import BaseModel

import sys
import asyncio
import subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.protocol_constants import (
    DEVICE_INVERTER, DEVICE_PROTECTION_RELAY,
    CTRL_INV_ON_OFF, CTRL_INV_ACTIVE_POWER, CTRL_INV_POWER_FACTOR,
    CTRL_INV_REACTIVE_POWER, CTRL_INV_CONTROL_INIT, CTRL_INV_CONTROL_CHECK,
    CTRL_INV_IV_SCAN, CTRL_RTU_REBOOT, CTRL_INV_MODEL, CTRL_RTU_INFO,
    CONTROL_TYPE_NAMES, INV_MODEL_NAMES,
    DEFAULT_FTP_HOST, DEFAULT_FTP_PORT, DEFAULT_FTP_USER, DEFAULT_FTP_PASSWORD,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# Module-level references set by main.py at startup
engine = None   # UDPEngine instance
database = None  # DB instance
ws = None        # WSManager instance

FIRMWARE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pc_programs", "firmware")

# --- Security: credentials from environment variables ---
SSH_USER = os.environ.get('RTU_SSH_USER', 'pi')
SSH_PASS = os.environ.get('RTU_SSH_PASS', 'raspberry')
SSH_PORT = int(os.environ.get('RTU_SSH_PORT', '22'))
FTP_USER_ENV = os.environ.get('RTU_FTP_USER', 'rtu')
FTP_PASS_ENV = os.environ.get('RTU_FTP_PASS', '1234')

# SSH 기본값: raspberry (환경변수 RTU_SSH_PASS로 변경 가능)

# --- Security: path whitelist for config file access ---
ALLOWED_CONFIG_DIRS = ['/home/pi/config', '/home/pi/common']

# --- Firmware upload constraints ---
MAX_FIRMWARE_SIZE = 500 * 1024 * 1024  # 500 MB

# --- Server start time for health endpoint ---
_server_start_time = time.time()

VERSION = "1.2.0"

# --- Local mode: RTU is on the same PC (simulator mode, no SSH needed) ---
_LOCAL_MODE_IPS = {'localhost', '127.0.0.1', 'local', ''}

def _is_local_mode(rtu_ip: str) -> bool:
    return rtu_ip.strip().lower() in _LOCAL_MODE_IPS


_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])$"
)


def _normalize_rtu_host(raw: str) -> str:
    host = (raw or "").strip()
    if not host:
        return ""
    if host.lower() in _LOCAL_MODE_IPS:
        return host.lower()
    if any(ch.isspace() for ch in host):
        raise HTTPException(status_code=400, detail="RTU IP/host must not contain spaces")
    if "://" in host or "/" in host or "\\" in host:
        raise HTTPException(status_code=400, detail="RTU IP/host must be a plain host or IPv4 address")
    if ":" in host:
        raise HTTPException(status_code=400, detail="RTU IP/host must not include a port")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if host.endswith(".") or ".." in host or not _HOSTNAME_RE.fullmatch(host):
            raise HTTPException(status_code=400, detail=f"Invalid RTU IP/host: {host}")
        return host

def _remote_to_local_path(remote_path: str) -> str:
    """Map /home/pi/{config|common}/X → local project filesystem path (security-checked)."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dir_map = {'/home/pi/config': 'config', '/home/pi/common': 'common'}
    for remote_dir, local_sub in dir_map.items():
        if remote_path.startswith(remote_dir + '/') or remote_path == remote_dir:
            rel = remote_path[len(remote_dir):].lstrip('/')
            base = os.path.realpath(os.path.join(project_root, local_sub))
            target = os.path.realpath(os.path.join(base, rel)) if rel else base
            if not (target == base or target.startswith(base + os.sep)):
                raise HTTPException(status_code=403, detail="Path traversal detected")
            return target
    raise HTTPException(status_code=403, detail=f"Unmappable remote path: {remote_path}")

async def _local_restart_rtu_process() -> int:
    """Terminate existing rtu_client process and restart it locally. Returns kill count."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    killed = 0
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = ' '.join(proc.info['cmdline'] or [])
                if 'rtu_client' in cmdline and proc.pid != os.getpid():
                    proc.terminate()
                    killed += 1
            except Exception:
                pass
    except ImportError:
        pass  # psutil not available
    if killed:
        await asyncio.sleep(1.5)
    popen_kwargs = {
        'cwd': project_root,
        'stdout': subprocess.DEVNULL,
        'stderr': subprocess.DEVNULL,
    }
    if sys.platform == 'win32':
        popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    else:
        popen_kwargs['start_new_session'] = True
    subprocess.Popen([sys.executable, '-m', 'rtu_program.rtu_client'], **popen_kwargs)
    return killed


# =========================================================================
# Validation Helpers
# =========================================================================

def _validate_path(path: str) -> str:
    """Validate that a path is within the allowed config directories."""
    # Normalize the path (resolve ..)
    normalized = os.path.normpath(path).replace('\\', '/')
    if not any(normalized.startswith(d) for d in ALLOWED_CONFIG_DIRS):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be under one of {ALLOWED_CONFIG_DIRS}"
        )
    return normalized


def _validate_timestamp(ts: Optional[str], param_name: str) -> Optional[str]:
    """Validate ISO format timestamp string."""
    if ts is None:
        return None
    try:
        datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid timestamp format for '{param_name}': expected ISO format (e.g. 2024-01-15T10:30:00)"
        )
    return ts


def _sanitize_filename(filename: str) -> str:
    """Sanitize a filename: reject path traversal and separators."""
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename: path traversal not allowed")
    return filename


def _validate_firmware_filename(filename: str) -> str:
    """Validate firmware filename: must be .tar.gz, no path traversal."""
    filename = _sanitize_filename(filename)
    if not filename.endswith('.tar.gz'):
        raise HTTPException(status_code=400, detail="Firmware file must be .tar.gz")
    return filename


# =========================================================================
# Pydantic Models
# =========================================================================

class ControlCommand(BaseModel):
    rtu_id: int
    device_num: int = 1
    value: int = 0


class FirmwareUpdate(BaseModel):
    rtu_id: int
    filename: str
    ftp_port: int = DEFAULT_FTP_PORT
    # ftp_host, ftp_user, ftp_pass는 환경변수(FTP_LOCAL_IP, FTP_USER_ENV, FTP_PASS_ENV)로만 설정


# =========================================================================
# Health Endpoint
# =========================================================================

@router.get("/health")
async def health_check():
    """Health check endpoint returning status, uptime, version, and stats."""
    uptime = time.time() - _server_start_time
    stats = {}
    if engine:
        stats['connected_rtus'] = len(engine.rtu_registry)
        stats['engine_running'] = True
    else:
        stats['connected_rtus'] = 0
        stats['engine_running'] = False
    stats['database_available'] = database is not None
    stats['websocket_available'] = ws is not None

    return {
        "status": "ok",
        "uptime_seconds": round(uptime, 1),
        "version": VERSION,
        "stats": stats,
    }


# =========================================================================
# RTU Endpoints
# =========================================================================

@router.get("/rtus")
async def list_rtus():
    """List all RTUs from both the live engine registry and the database."""
    live = engine.get_rtu_list() if engine else []
    db_rtus = await database.get_rtus() if database else []

    # Merge: live data takes priority
    live_ids = {r['rtu_id'] for r in live}
    merged = list(live)
    for db_rtu in db_rtus:
        if db_rtu['rtu_id'] not in live_ids:
            db_rtu['status'] = 'offline'
            merged.append(db_rtu)

    return {"rtus": merged}


@router.get("/rtus/{rtu_id}")
async def get_rtu_detail(rtu_id: int):
    """Get detailed info for a specific RTU."""
    # Try live data first
    live_list = engine.get_rtu_list() if engine else []
    live_info = next((r for r in live_list if r['rtu_id'] == rtu_id), None)

    db_info = await database.get_rtu(rtu_id) if database else None

    if not live_info and not db_info:
        raise HTTPException(status_code=404, detail=f"RTU {rtu_id} not found")

    result = db_info or {}
    if live_info:
        result.update(live_info)

    return result


@router.delete("/rtus/{rtu_id}")
async def delete_rtu(rtu_id: int):
    """Hide RTU from dashboard (keeps historical data, suppresses re-display on reconnect)."""
    removed_memory = False

    # Remove from engine memory (stop processing new packets for display)
    if engine:
        with engine._lock:
            if rtu_id in engine.rtu_registry:
                del engine.rtu_registry[rtu_id]
                removed_memory = True

    # Mark as hidden in DB (data retained, won't reappear on restart or reconnect)
    if database:
        try:
            await database.delete_rtu(rtu_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"DB error: {e}")

    logger.info(f"RTU {rtu_id} hidden (memory={removed_memory})")
    return {"status": "deleted", "rtu_id": rtu_id, "removed_memory": removed_memory}


@router.get("/rtus/{rtu_id}/devices")
async def get_rtu_devices(rtu_id: int):
    """Get all devices and their latest data for a specific RTU."""
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not available")

    devices = engine.get_rtu_devices(rtu_id)
    if devices is None:
        raise HTTPException(status_code=404, detail=f"RTU {rtu_id} not found")

    # Enrich inverter devices with latest control status & monitor data
    if database:
        for dk, dev in devices.items():
            if not isinstance(dev, dict) or 'data' not in dev:
                continue
            dev_type = dev.get('device_type', 0)
            dev_num = dev.get('device_number', 0)
            if dev_type == 1 and dev_num > 0:
                try:
                    ctrl = await database.get_latest_control_status(rtu_id, dev_num)
                    mon = await database.get_latest_control_monitor(rtu_id, dev_num)
                    if ctrl:
                        dev['data']['ctrl'] = ctrl
                    if mon:
                        dev['data']['mon'] = mon
                except Exception:
                    pass

    return {"rtu_id": rtu_id, "devices": devices}


@router.get("/rtus/{rtu_id}/iv_scan")
async def get_iv_scan_data(rtu_id: int):
    """Get latest IV Scan data for a specific RTU."""
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not available")

    data = engine.get_iv_scan_data(rtu_id)
    if data is None:
        return {"rtu_id": rtu_id, "available": False}

    return {
        "rtu_id": rtu_id,
        "available": True,
        "device_number": data['dev_num'],
        "model": data['model'],
        "total_strings": data['total_strings'],
        "timestamp": data['timestamp'],
        "strings": {str(k): v for k, v in sorted(data['strings'].items())},
    }


@router.post("/rtus/{rtu_id}/iv_scan/save")
async def save_iv_scan_csv(rtu_id: int):
    """Save latest IV Scan data to IVdata/ directory as CSV."""
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not available")

    data = engine.get_iv_scan_data(rtu_id)
    if data is None:
        raise HTTPException(status_code=404, detail="No IV scan data")

    from common.protocol_constants import DEVICE_TYPE_NAMES

    iv_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'IVdata')
    os.makedirs(iv_dir, exist_ok=True)

    model_str = INV_MODEL_NAMES.get(data['model'], f'Model{data["model"]}').replace(' ', '_')
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    fname = f"{ts}-{rtu_id}-{model_str}_IV.csv"
    fpath = os.path.join(iv_dir, fname)

    with open(fpath, 'w', encoding='utf-8') as f:
        f.write('String,Point,Voltage(V),Current(A)\n')
        for str_num in sorted(data['strings'].keys()):
            for i, pt in enumerate(data['strings'][str_num]):
                f.write(f"{str_num},{i+1},{pt['voltage']},{pt['current']}\n")

    return {"status": "saved", "filename": fname, "path": fpath}


@router.get("/iv_scan/files")
async def list_iv_scan_files():
    """List all saved IV Scan CSV files."""
    iv_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'IVdata')
    if not os.path.isdir(iv_dir):
        return {"files": []}
    files = sorted([f for f in os.listdir(iv_dir) if f.endswith('.csv')], reverse=True)
    return {"files": files}


@router.get("/iv_scan/files/{filename}")
async def get_iv_scan_file(filename: str):
    """Read a saved IV Scan CSV file and return parsed data."""
    # Security: validate filename
    _sanitize_filename(filename)

    iv_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'IVdata')
    fpath = os.path.join(iv_dir, filename)
    if not os.path.isfile(fpath):
        raise HTTPException(status_code=404, detail="File not found")

    strings = {}
    with open(fpath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith('String') or not line:
                continue
            parts = line.split(',')
            if len(parts) < 4:
                continue
            sn = parts[0].strip()
            v = float(parts[2])
            c = float(parts[3])
            if sn not in strings:
                strings[sn] = []
            strings[sn].append({'voltage': v, 'current': c})

    # Parse filename: YYYYMMDD_HHMMSS-rtuId-model_IV.csv
    name_parts = filename.replace('.csv', '').split('-')
    model_name = name_parts[2].replace('_IV', '') if len(name_parts) >= 3 else 'Unknown'
    rtu_id = name_parts[1] if len(name_parts) >= 2 else '0'

    return {
        "available": True,
        "filename": filename,
        "rtu_id": rtu_id,
        "model_name": model_name,
        "device_number": 0,
        "model": 0,
        "total_strings": len(strings),
        "strings": strings,
    }


# =========================================================================
# Data Endpoints
# =========================================================================

@router.get("/data/inverter")
async def get_inverter_data(
    rtu_id: Optional[int] = Query(None),
    device_num: Optional[int] = Query(None),
    from_ts: Optional[str] = Query(None),
    to_ts: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=10000),
):
    """Query inverter data history."""
    if not database:
        raise HTTPException(status_code=503, detail="Database not available")
    _validate_timestamp(from_ts, "from_ts")
    _validate_timestamp(to_ts, "to_ts")
    rows = await database.get_inverter_history(rtu_id, device_num, from_ts, to_ts, limit)
    return {"data": rows, "count": len(rows)}


@router.get("/data/relay")
async def get_relay_data(
    rtu_id: Optional[int] = Query(None),
    device_num: Optional[int] = Query(None),
    from_ts: Optional[str] = Query(None),
    to_ts: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=10000),
):
    """Query relay data history."""
    if not database:
        raise HTTPException(status_code=503, detail="Database not available")
    _validate_timestamp(from_ts, "from_ts")
    _validate_timestamp(to_ts, "to_ts")
    rows = await database.get_relay_history(rtu_id, device_num, from_ts, to_ts, limit)
    return {"data": rows, "count": len(rows)}


@router.get("/data/weather")
async def get_weather_data(
    rtu_id: Optional[int] = Query(None),
    device_num: Optional[int] = Query(None),
    from_ts: Optional[str] = Query(None),
    to_ts: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=10000),
):
    """Query weather station data history."""
    if not database:
        raise HTTPException(status_code=503, detail="Database not available")
    _validate_timestamp(from_ts, "from_ts")
    _validate_timestamp(to_ts, "to_ts")
    rows = await database.get_weather_history(rtu_id, device_num, from_ts, to_ts, limit)
    return {"data": rows, "count": len(rows)}


# =========================================================================
# Control Endpoints
# =========================================================================

_CONTROL_COMMANDS_NEEDING_CHECK = {
    CTRL_INV_ON_OFF, CTRL_INV_ACTIVE_POWER, CTRL_INV_POWER_FACTOR,
    CTRL_INV_REACTIVE_POWER, CTRL_INV_CONTROL_INIT,
}

async def _send_control(cmd: ControlCommand, ctrl_type: int, dev_type: int = DEVICE_INVERTER):
    """Helper to send an H03 control command."""
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not available")
    ctrl_name = CONTROL_TYPE_NAMES.get(ctrl_type, f"ctrl={ctrl_type}")
    # Mark as manual command so responses show in UI (not suppressed by periodic check)
    from web_server_prod.main import mark_manual_command
    mark_manual_command(cmd.rtu_id, cmd.device_num)

    ok = engine.send_h03(cmd.rtu_id, ctrl_type, dev_type, cmd.device_num, cmd.value)
    if not ok:
        raise HTTPException(status_code=404, detail=f"RTU {cmd.rtu_id} not found or send failed")
    if database:
        await database.save_event(
            cmd.rtu_id, "H03_SENT",
            f"{ctrl_name} dev={cmd.device_num} val={cmd.value}")
    if ws:
        await ws.broadcast({
            "type": "event", "rtu_id": cmd.rtu_id,
            "event_type": "H03_SENT",
            "detail": f"{ctrl_name} dev={cmd.device_num} val={cmd.value}"})

    # RTU automatically sends control_check + control_result after control commands,
    # so no need for server-side auto Status Check (was causing duplicates)

    return {
        "status": "sent",
        "rtu_id": cmd.rtu_id,
        "control_type": ctrl_type,
        "device_num": cmd.device_num,
        "value": cmd.value,
    }


@router.get("/control/status/{rtu_id}/{device_num}")
async def get_control_status(rtu_id: int, device_num: int):
    """Get latest control status for a device from DB."""
    if not database:
        raise HTTPException(status_code=503, detail="DB not available")
    row = await database.get_latest_control_status(rtu_id, device_num)
    if not row:
        return {"on_off": 0, "power_factor": 1000, "operation_mode": 0,
                "reactive_power_pct": 0, "active_power_pct": 1000}
    return row


@router.post("/control/on_off")
async def control_on_off(cmd: ControlCommand):
    """Send inverter ON/OFF control (value 0=ON, 1=OFF)."""
    return await _send_control(cmd, CTRL_INV_ON_OFF)


@router.post("/control/active_power")
async def control_active_power(cmd: ControlCommand):
    """Send active power limit (value 0-1000 = 0-100.0%)."""
    return await _send_control(cmd, CTRL_INV_ACTIVE_POWER)


@router.post("/control/power_factor")
async def control_power_factor(cmd: ControlCommand):
    """Send power factor control (value -1000 to 1000 = -1.0 to 1.0)."""
    return await _send_control(cmd, CTRL_INV_POWER_FACTOR)


@router.post("/control/reactive_power")
async def control_reactive_power(cmd: ControlCommand):
    """Send reactive power control."""
    return await _send_control(cmd, CTRL_INV_REACTIVE_POWER)


@router.post("/control/init")
async def control_init(cmd: ControlCommand):
    """Send control init command."""
    return await _send_control(cmd, CTRL_INV_CONTROL_INIT)


@router.post("/control/check")
async def control_check(cmd: ControlCommand):
    """Send control check command."""
    return await _send_control(cmd, CTRL_INV_CONTROL_CHECK)


@router.post("/control/iv_scan")
async def control_iv_scan(cmd: ControlCommand):
    """Send IV scan command."""
    return await _send_control(cmd, CTRL_INV_IV_SCAN)


@router.post("/control/reboot")
async def control_reboot(cmd: ControlCommand):
    """Send RTU reboot command."""
    return await _send_control(cmd, CTRL_RTU_REBOOT, dev_type=0)


@router.post("/control/model_info")
async def control_model_info(cmd: ControlCommand):
    """Request inverter model info."""
    return await _send_control(cmd, CTRL_INV_MODEL)


@router.post("/control/rtu_info")
async def control_rtu_info(cmd: ControlCommand):
    """Request RTU info."""
    return await _send_control(cmd, CTRL_RTU_INFO, dev_type=0)


# =========================================================================
# Firmware Endpoints
# =========================================================================

@router.get("/firmware/list")
async def firmware_list():
    """List available firmware files."""
    if not os.path.isdir(FIRMWARE_DIR):
        return {"files": []}

    FIRMWARE_EXTENSIONS = ('.tar.gz', '.gz', '.zip', '.tar')
    files = []
    for fname in os.listdir(FIRMWARE_DIR):
        # 펌웨어 확장자만 반환
        if not any(fname.endswith(ext) for ext in FIRMWARE_EXTENSIONS):
            continue
        fpath = os.path.join(FIRMWARE_DIR, fname)
        if os.path.isfile(fpath):
            stat = os.stat(fpath)
            files.append({
                "filename": fname,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
    return {"files": files}


@router.post("/firmware/upload")
async def firmware_upload(file: UploadFile):
    """Upload a firmware file. Only .tar.gz, max 500MB, sanitized filename."""
    # Security: validate filename
    filename = _validate_firmware_filename(file.filename or "")

    os.makedirs(FIRMWARE_DIR, exist_ok=True)

    # Security: read with size limit
    content = await file.read()
    if len(content) > MAX_FIRMWARE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Firmware file too large: {len(content)} bytes (max {MAX_FIRMWARE_SIZE})"
        )

    dest = os.path.join(FIRMWARE_DIR, filename)
    with open(dest, 'wb') as f:
        f.write(content)
    return {"filename": filename, "size": len(content), "status": "uploaded"}


FTP_LOCAL_IP = "127.0.0.1"  # Set by main.py at startup

@router.post("/firmware/update")
async def firmware_update(fw: FirmwareUpdate):
    """Send H07 firmware update command to an RTU using built-in FTP."""
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not available")

    # Security: validate firmware filename
    _validate_firmware_filename(fw.filename)

    # Use built-in FTP server with credentials from environment
    ftp_host = FTP_LOCAL_IP
    ftp_port = fw.ftp_port
    ftp_user = FTP_USER_ENV
    ftp_pass = FTP_PASS_ENV
    ftp_path = "/"

    ok = engine.send_h07(
        fw.rtu_id, ftp_host, ftp_port,
        ftp_user, ftp_pass, ftp_path, fw.filename
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"RTU {fw.rtu_id} not found or send failed")

    if database:
        await database.save_event(
            fw.rtu_id, "FW_UPDATE",
            f"Sent H07: {ftp_user}@{ftp_host}:{ftp_port}/{fw.filename}")

    return {"status": "sent", "rtu_id": fw.rtu_id, "filename": fw.filename,
            "ftp": f"{ftp_user}@{ftp_host}:{ftp_port}/{fw.filename}"}


# =========================================================================
# Config (SSH to RTU) — Secured
# =========================================================================

RTU_DIRS = ["/home/pi/config", "/home/pi/common"]

def _ssh_connect(ip: str):
    import paramiko
    ip = _normalize_rtu_host(ip)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    logger.warning("SSH host key not verified (AutoAddPolicy). Set RTU_SSH_KNOWN_HOSTS for production.")
    # Try key auth first, then password
    key_path = os.path.expanduser("~/.ssh/id_rsa")
    try:
        if os.path.isfile(key_path):
            client.connect(ip, port=SSH_PORT, username=SSH_USER, key_filename=key_path, timeout=10)
        else:
            client.connect(ip, port=SSH_PORT, username=SSH_USER, password=SSH_PASS, timeout=10)
    except Exception:
        client.connect(ip, port=SSH_PORT, username=SSH_USER, password=SSH_PASS, timeout=10)
    return client


@router.get("/config/files")
async def config_list_files(rtu_ip: str = Query(...)):
    """List all config/program files from local filesystem (SSH not used)."""
    ALLOWED_EXTENSIONS = {'.ini', '.py', '.md', '.json', '.txt'}
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = []
    grouped = {}
    for local_sub, remote_dir in [('config', '/home/pi/config'), ('common', '/home/pi/common')]:
        local_dir = os.path.join(project_root, local_sub)
        grouped[local_sub] = []
        if not os.path.isdir(local_dir):
            continue
        for fname in sorted(os.listdir(local_dir)):
            _, ext = os.path.splitext(fname)
            if ext.lower() not in ALLOWED_EXTENSIONS:
                continue
            if not os.path.isfile(os.path.join(local_dir, fname)):
                continue
            remote_path = f"{remote_dir}/{fname}"
            files.append(remote_path)
            grouped[local_sub].append(remote_path)
    return {"files": files, "grouped": grouped}


@router.get("/config/read")
async def config_read_file(rtu_ip: str = Query(...), path: str = Query(...)):
    """Read a file from local filesystem (SSH not used)."""
    validated_path = _validate_path(path)
    local_path = _remote_to_local_path(validated_path)
    try:
        with open(local_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        return {"path": validated_path, "content": content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {local_path}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Local read error: {e}")


class ConfigWrite(BaseModel):
    rtu_ip: str
    path: str
    content: str

@router.post("/config/write")
async def config_write_file(req: ConfigWrite):
    """Write a file to local filesystem (SSH not used)."""
    validated_path = _validate_path(req.path)
    local_path = _remote_to_local_path(validated_path)
    try:
        with open(local_path, 'w', encoding='utf-8') as f:
            f.write(req.content)
        if database:
            await database.save_event(0, "CONFIG_WRITE", f"LOCAL:{validated_path}")
        return {"status": "saved", "path": validated_path}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Local write error: {e}")


class ConfigUpload(BaseModel):
    rtu_ip: str
    directories: list = ["config", "common"]  # PC local directories to upload

@router.post("/config/upload")
async def config_upload_to_rtu(req: ConfigUpload):
    """Upload local PC config/common files to RTU via SFTP, or confirm local in simulator mode."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    req.rtu_ip = _normalize_rtu_host(req.rtu_ip)

    if _is_local_mode(req.rtu_ip):
        # Local mode: PC and RTU share the same filesystem — files are already in place
        uploaded = []
        exts = {'.ini', '.py', '.json', '.txt'}
        for dir_name in req.directories:
            safe_dir = _sanitize_filename(dir_name)
            local_dir = os.path.join(project_root, safe_dir)
            if not os.path.isdir(local_dir):
                continue
            for fname in sorted(os.listdir(local_dir)):
                if any(fname.endswith(e) for e in exts) and not fname.startswith('__'):
                    uploaded.append(f"{safe_dir}/{fname}")
        if database:
            await database.save_event(0, "CONFIG_UPLOAD",
                f"LOCAL: {len(uploaded)} files confirmed")
        return {"status": "uploaded", "uploaded": uploaded, "errors": [],
                "count": len(uploaded), "mode": "local"}

    client = None
    sftp = None
    try:
        client = _ssh_connect(req.rtu_ip)
        sftp = client.open_sftp()

        uploaded = []
        errors = []
        for dir_name in req.directories:
            # Security: only allow known directory names (no path traversal)
            safe_dir = _sanitize_filename(dir_name)
            local_dir = os.path.join(project_root, safe_dir)
            remote_dir = f"/home/pi/{safe_dir}"
            if not os.path.isdir(local_dir):
                continue

            # Ensure remote directory exists
            try:
                sftp.stat(remote_dir)
            except FileNotFoundError:
                sftp.mkdir(remote_dir)

            # Upload matching files
            exts = {'.ini', '.py', '.json', '.txt'}
            skip = {'__pycache__', '.pyc'}
            for fname in sorted(os.listdir(local_dir)):
                if not any(fname.endswith(e) for e in exts):
                    continue
                if any(s in fname for s in skip):
                    continue
                local_path = os.path.join(local_dir, fname)
                remote_path = f"{remote_dir}/{fname}"
                try:
                    sftp.put(local_path, remote_path)
                    uploaded.append(f"{safe_dir}/{fname}")
                except Exception as fe:
                    errors.append(f"{safe_dir}/{fname}: {fe}")

        if database:
            await database.save_event(0, "CONFIG_UPLOAD",
                f"{req.rtu_ip}: {len(uploaded)} files uploaded")
        return {"status": "uploaded", "uploaded": uploaded, "errors": errors,
                "count": len(uploaded)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SSH/SFTP error: {e}")
    finally:
        if sftp:
            try: sftp.close()
            except Exception: pass
        if client:
            try: client.close()
            except Exception: pass


class ConfigRestart(BaseModel):
    rtu_ip: str

@router.post("/config/restart")
async def config_restart_rtu(req: ConfigRestart):
    """Restart RTU service via SSH, or restart local rtu_client process in simulator mode."""
    req.rtu_ip = _normalize_rtu_host(req.rtu_ip)
    if _is_local_mode(req.rtu_ip):
        try:
            killed = await _local_restart_rtu_process()
            if database:
                await database.save_event(0, "RTU_RESTART",
                    f"LOCAL restart (terminated={killed})")
            return {"status": "restarted", "rtu_ip": "localhost", "mode": "local"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Local restart error: {e}")

    client = None
    try:
        client = _ssh_connect(req.rtu_ip)
        _, stdout, stderr = client.exec_command('sudo systemctl restart rtu')
        stdout.read()
        if database:
            await database.save_event(0, "RTU_RESTART", f"SSH restart: {req.rtu_ip}")
        return {"status": "restarted", "rtu_ip": req.rtu_ip}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SSH error: {e}")
    finally:
        if client:
            try: client.close()
            except Exception: pass


@router.get("/config/push_preview")
async def config_push_preview():
    """List PC operational files that will be pushed to RTU:
    config/device_models.ini, config/rs485_ch*.ini, common/*_registers.py"""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = []

    # config/rtu_config.ini
    p = os.path.join(project_root, 'config', 'rtu_config.ini')
    if os.path.isfile(p):
        files.append({'local': 'config/rtu_config.ini',
                      'remote': '/home/pi/config/rtu_config.ini',
                      'size': os.path.getsize(p)})

    # config/device_models.ini
    p = os.path.join(project_root, 'config', 'device_models.ini')
    if os.path.isfile(p):
        files.append({'local': 'config/device_models.ini',
                      'remote': '/home/pi/config/device_models.ini',
                      'size': os.path.getsize(p)})

    # config/rs485_ch*.ini (operational only: no simulator / bak / test)
    config_dir = os.path.join(project_root, 'config')
    if os.path.isdir(config_dir):
        for fname in sorted(os.listdir(config_dir)):
            if not (fname.startswith('rs485_ch') and fname.endswith('.ini')):
                continue
            if any(x in fname for x in ('simulator', '.bak', '_test')):
                continue
            p = os.path.join(config_dir, fname)
            files.append({'local': f'config/{fname}',
                          'remote': f'/home/pi/config/{fname}',
                          'size': os.path.getsize(p)})

    # common/*_registers.py (operational only: no _test)
    common_dir = os.path.join(project_root, 'common')
    if os.path.isdir(common_dir):
        for fname in sorted(os.listdir(common_dir)):
            if not fname.endswith('_registers.py'):
                continue
            if '_test' in fname or fname.startswith('__'):
                continue
            fp = os.path.join(common_dir, fname)
            if os.path.isfile(fp):
                files.append({'local': f'common/{fname}',
                              'remote': f'/home/pi/common/{fname}',
                              'size': os.path.getsize(fp)})

    return {'files': files, 'count': len(files)}


@router.post("/config/push_to_rtu")
async def config_push_to_rtu(req: ConfigRestart):
    """Push operational config + registers files from PC to RTU, then restart service."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    req.rtu_ip = _normalize_rtu_host(req.rtu_ip)
    results = []

    # Build file pairs (local_path, remote_path, display_name) — shared by both modes
    file_pairs = []
    p = os.path.join(project_root, 'config', 'rtu_config.ini')
    if os.path.isfile(p):
        file_pairs.append((p, '/home/pi/config/rtu_config.ini', 'config/rtu_config.ini'))
    p = os.path.join(project_root, 'config', 'device_models.ini')
    if os.path.isfile(p):
        file_pairs.append((p, '/home/pi/config/device_models.ini', 'config/device_models.ini'))
    config_dir = os.path.join(project_root, 'config')
    if os.path.isdir(config_dir):
        for fname in sorted(os.listdir(config_dir)):
            if not (fname.startswith('rs485_ch') and fname.endswith('.ini')):
                continue
            if any(x in fname for x in ('simulator', '.bak', '_test')):
                continue
            file_pairs.append((os.path.join(config_dir, fname),
                               f'/home/pi/config/{fname}', f'config/{fname}'))
    common_dir = os.path.join(project_root, 'common')
    if os.path.isdir(common_dir):
        for fname in sorted(os.listdir(common_dir)):
            if not fname.endswith('_registers.py'):
                continue
            if '_test' in fname or fname.startswith('__'):
                continue
            fp = os.path.join(common_dir, fname)
            if os.path.isfile(fp):
                file_pairs.append((fp, f'/home/pi/common/{fname}', f'common/{fname}'))

    if _is_local_mode(req.rtu_ip):
        # Local mode: files are already on this PC — just verify existence then restart
        for local_path, _, display_name in file_pairs:
            if os.path.isfile(local_path):
                results.append({'file': display_name, 'status': 'ok'})
            else:
                results.append({'file': display_name, 'status': 'error', 'error': 'not found'})
        await _local_restart_rtu_process()
        ok_count = sum(1 for r in results if r['status'] == 'ok')
        if database:
            await database.save_event(0, "CONFIG_PUSH",
                f"LOCAL: {ok_count}/{len(results)} files, restart=ok")
        return {
            'status': 'done',
            'results': results,
            'ok_count': ok_count,
            'error_count': len(results) - ok_count,
            'restart': 'ok'
        }

    client = None
    sftp = None
    try:
        client = _ssh_connect(req.rtu_ip)
        sftp = client.open_sftp()

        for local_path, remote_path, display_name in file_pairs:
            try:
                sftp.put(local_path, remote_path)
                results.append({'file': display_name, 'status': 'ok'})
            except Exception as fe:
                results.append({'file': display_name, 'status': 'error', 'error': str(fe)})

        sftp.close()
        sftp = None

        restart_ok = False
        restart_error = ''
        try:
            _, stdout, stderr = client.exec_command('sudo systemctl restart rtu')
            stdout.read()
            err_output = stderr.read().decode('utf-8', errors='replace').strip()
            if err_output:
                restart_error = err_output
            else:
                restart_ok = True
        except Exception as re_err:
            restart_error = str(re_err)

        ok_count = sum(1 for r in results if r['status'] == 'ok')
        if database:
            await database.save_event(0, "CONFIG_PUSH",
                f"{req.rtu_ip}: {ok_count}/{len(results)} files, "
                f"restart={'ok' if restart_ok else 'fail'}")
        return {
            'status': 'done',
            'results': results,
            'ok_count': ok_count,
            'error_count': len(results) - ok_count,
            'restart': 'ok' if restart_ok else f'failed: {restart_error}'
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SSH/SFTP error: {e}")
    finally:
        if sftp:
            try: sftp.close()
            except Exception: pass
        if client:
            try: client.close()
            except Exception: pass


# =========================================================================
# Events & Stats
# =========================================================================

@router.get("/events")
async def get_events(
    rtu_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    from_ts: Optional[str] = Query(None),
):
    """Query the event log."""
    if not database:
        raise HTTPException(status_code=503, detail="Database not available")
    events = await database.get_events(rtu_id, limit, offset, from_ts=from_ts)
    return {"events": events, "count": len(events)}


@router.get("/stats")
async def get_stats():
    """Return server statistics."""
    stats = dict(engine.stats) if engine else {}
    uptime = time.time() - stats.get('start_time', time.time())
    stats['uptime_seconds'] = round(uptime, 1)
    stats['uptime'] = round(uptime, 1)
    stats['connected_rtus'] = len(engine.rtu_registry) if engine else 0
    stats['rtu_count'] = stats['connected_rtus']

    if engine:
        now = time.time()
        active = 0
        with engine._lock:
            for state in engine.rtu_registry.values():
                age = now - state.last_seen
                interval = getattr(state, 'avg_interval', 0)
                threshold = min(max(interval * 2, 120) if interval > 0 else 360, 660)
                if age < threshold:
                    active += 1
        stats['active_connections'] = active
        stats['iv_scan_count'] = len(engine.iv_scan_data)
    else:
        stats['active_connections'] = 0
        stats['iv_scan_count'] = 0

    stats['total_packets'] = (
        stats.get('h01_received', 0) + stats.get('h02_sent', 0) +
        stats.get('h03_sent', 0) + stats.get('h04_received', 0) +
        stats.get('h05_received', 0) + stats.get('h06_sent', 0) +
        stats.get('h08_received', 0)
    )
    return stats
