# -*- coding: utf-8 -*-
"""
세션 저장소 — 진행 중인 Stage 작업 상태 관리
각 세션은 UUID로 식별, temp/{session_id}/ 디렉토리에 파일 저장
"""
import uuid
import os
import json
from datetime import datetime

TEMP_DIR = os.path.join(os.path.dirname(__file__), '..', 'temp')


class SessionStore:
    _sessions: dict = {}

    @classmethod
    def create(cls) -> str:
        sid = str(uuid.uuid4())
        cls._sessions[sid] = {
            'id': sid,
            'created': datetime.now().isoformat(),
            'stage': 0,           # 0=초기, 1=Stage1완료, 2=Stage2완료, 3=Stage3완료
            'stage1_excel': None,
            'stage2_excel': None,
            'registers_py': None,
            'meta': {},
        }
        os.makedirs(os.path.join(TEMP_DIR, sid), exist_ok=True)
        return sid

    @classmethod
    def get(cls, sid: str) -> dict | None:
        return cls._sessions.get(sid)

    @classmethod
    def update(cls, sid: str, **kwargs):
        if sid in cls._sessions:
            cls._sessions[sid].update(kwargs)

    @classmethod
    def get_work_dir(cls, sid: str) -> str:
        path = os.path.join(TEMP_DIR, sid)
        os.makedirs(path, exist_ok=True)
        return path

    @classmethod
    def cancel_running_task(cls, sid: str):
        """진행 중인 stage 태스크 취소 (새 파일 업로드 시 호출)"""
        s = cls._sessions.get(sid)
        if not s:
            return
        task = s.get('_running_task')
        if task and not task.done():
            task.cancel()
        s.pop('_running_task', None)
