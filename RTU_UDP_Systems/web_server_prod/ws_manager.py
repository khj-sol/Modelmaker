"""
WebSocket Broadcast Manager (Production)
Thread-safe WebSocket management with zombie connection cleanup.
"""

import json
import logging
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WSManager:
    """Manages WebSocket connections and broadcasts messages to all clients."""

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
            text = json.dumps(message, default=str)
        except (TypeError, ValueError) as e:
            logger.error(f"WS JSON serialization failed: {e}")
            return

        disconnected = []

        # Iterate over a copy to avoid modification during iteration
        for connection in list(self.active_connections):
            try:
                await connection.send_text(text)
            except Exception as e:
                logger.debug(f"WS send failed: {e}")
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)
