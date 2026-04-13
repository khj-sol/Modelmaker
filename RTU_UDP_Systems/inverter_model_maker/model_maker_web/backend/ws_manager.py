# -*- coding: utf-8 -*-
"""
WebSocket Manager - Model Maker Web v1.0.0

Manages WebSocket connections and broadcasts progress messages
to all connected clients (session-aware).
"""

import json
import logging
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WSManager:
    """Manages WebSocket connections and broadcasts messages."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WS connected. Clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WS disconnected. Clients: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        if not self.active_connections:
            return
        try:
            text = json.dumps(message, default=str, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            logger.error(f"WS JSON serialization failed: {e}")
            return
        disconnected = []
        for connection in list(self.active_connections):
            try:
                await connection.send_text(text)
            except Exception as e:
                logger.debug(f"WS send failed: {e}")
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)

    async def send_progress(self, session_id: str, stage: str, message: str, pct: int = -1):
        """Send a stage progress message to all clients."""
        await self.broadcast({
            "type": "progress",
            "session_id": session_id,
            "stage": stage,
            "message": message,
            "pct": pct,
        })

    async def send_done(self, session_id: str, stage: str, success: bool, detail: str = ""):
        """Send a stage completion message."""
        await self.broadcast({
            "type": "done",
            "session_id": session_id,
            "stage": stage,
            "success": success,
            "detail": detail,
        })

    async def send_error(self, session_id: str, stage: str, error: str):
        """Send a stage error message."""
        await self.broadcast({
            "type": "error",
            "session_id": session_id,
            "stage": stage,
            "error": error,
        })
