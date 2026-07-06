# -*- coding: utf-8 -*-
"""
WebSocket 관리자 — Stage 실행 중 실시간 로그 스트리밍
"""
from fastapi import WebSocket
from typing import Dict
import asyncio


class WSManager:
    def __init__(self):
        self.connections: Dict[str, WebSocket] = {}

    async def connect(self, sid: str, ws: WebSocket):
        await ws.accept()
        self.connections[sid] = ws

    def disconnect(self, sid: str):
        self.connections.pop(sid, None)

    async def send(self, sid: str, msg: str):
        ws = self.connections.get(sid)
        if ws:
            try:
                await ws.send_text(msg)
            except Exception:
                self.disconnect(sid)

    async def send_json(self, sid: str, data: dict):
        ws = self.connections.get(sid)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(sid)


ws_manager = WSManager()
