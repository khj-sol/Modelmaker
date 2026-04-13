# -*- coding: utf-8 -*-
"""
Model Maker Web - FastAPI Application Entry Point v1.0.0

Web-based version of the Model Maker Tkinter GUI.
Reuses stage_pipeline.py, ai_generator.py, reference_manager.py from model_maker/.
"""

import sys
import os
import asyncio
import logging
import time

# Ensure project root is on sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
_WEB_ROOT = os.path.dirname(_HERE)
_PROJECT_ROOT = os.path.dirname(_WEB_ROOT)
for _p in [_PROJECT_ROOT, _WEB_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from model_maker_web.backend.ws_manager import WSManager
from model_maker_web.backend.session_store import get_store
from model_maker_web.backend import api_routes

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("model_maker_web")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
WEB_PORT = int(os.environ.get("MM_WEB_PORT", "8181"))

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Model Maker Web", version="1.0.0")

ws_manager = WSManager()
store = get_store()

# Wire dependencies into api_routes
api_routes.ws_manager = ws_manager
api_routes.store = store

app.include_router(api_routes.router)

# ---------------------------------------------------------------------------
# Static Files
# ---------------------------------------------------------------------------
_STATIC_DIR = os.path.join(_WEB_ROOT, "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.middleware("http")
async def no_cache_js(request, call_next):
    response = await call_next(request)
    if request.url.path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/")
async def index():
    idx = os.path.join(_STATIC_DIR, "index.html")
    if os.path.isfile(idx):
        return FileResponse(idx)
    return JSONResponse({"message": "Model Maker Web API is running. No static/index.html found."})


@app.get("/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "version": "1.0.0",
        "uptime_seconds": round(time.time() - _start_time, 1),
        "sessions": len(store.all_ids()),
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
            await websocket.receive_text()  # keep-alive ping
    except Exception:
        ws_manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Background Tasks
# ---------------------------------------------------------------------------
_bg_tasks: list[asyncio.Task] = []


async def _session_cleanup_task():
    """Every 30 minutes: clean up expired sessions."""
    while True:
        try:
            await asyncio.sleep(1800)
            store.cleanup_expired()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Session cleanup error: {e}")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup():
    global _start_time
    _start_time = time.time()

    # Ensure temp directory exists
    temp_dir = os.path.join(_WEB_ROOT, "temp")
    os.makedirs(temp_dir, exist_ok=True)

    _bg_tasks.append(asyncio.create_task(_session_cleanup_task()))
    logger.info(f"Model Maker Web started on port {WEB_PORT}")


@app.on_event("shutdown")
async def shutdown():
    for task in _bg_tasks:
        task.cancel()
    if _bg_tasks:
        try:
            await asyncio.wait_for(
                asyncio.gather(*_bg_tasks, return_exceptions=True),
                timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            pass
    _bg_tasks.clear()
    logger.info("Model Maker Web shut down")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "model_maker_web.backend.main:app",
        host="0.0.0.0",
        port=WEB_PORT,
        log_level="info",
    )
