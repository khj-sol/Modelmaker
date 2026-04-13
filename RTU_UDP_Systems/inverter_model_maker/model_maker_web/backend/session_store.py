# -*- coding: utf-8 -*-
"""
Session Store - Model Maker Web v1.0.0

Per-session temp data management.
Each session tracks:
  - Uploaded PDF path
  - Stage 1 Excel path
  - Stage 2 mapping Excel path
  - Stage 3 generated code + validation results
  - Current running stage (for lock)
"""

import os
import uuid
import time
import logging
import threading

logger = logging.getLogger(__name__)

# Session TTL: 4 hours of inactivity
_SESSION_TTL = 4 * 3600

_TEMP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp")


class Session:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.created_at = time.time()
        self.last_active = time.time()

        # File paths
        self.pdf_path: str | None = None
        self.pdf_filename: str = ""
        self.stage1_excel_path: str | None = None
        self.stage2_excel_path: str | None = None

        # Stage 3 results
        self.stage3_code: str = ""
        self.stage3_results: list = []  # list of (status, msg)
        self.stage3_success: bool = False
        self.stage3_output_path: str | None = None

        # Stage 2 mapping data (cached JSON for frontend)
        self.stage2_rows: list = []

        # Stage 1 register data (cached JSON for frontend)
        self.stage1_rows: list = []

        # Running lock
        self.running: bool = False
        self.lock = threading.Lock()

    def touch(self):
        self.last_active = time.time()

    def session_dir(self) -> str:
        d = os.path.join(_TEMP_DIR, self.session_id)
        os.makedirs(d, exist_ok=True)
        return d


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self) -> Session:
        sid = str(uuid.uuid4())
        s = Session(sid)
        with self._lock:
            self._sessions[sid] = s
        logger.info(f"Session created: {sid}")
        return s

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            s = self._sessions.get(session_id)
        if s:
            s.touch()
        return s

    def get_or_create(self, session_id: str) -> Session:
        """Get existing session or create a new one with the given ID."""
        with self._lock:
            if session_id and session_id in self._sessions:
                s = self._sessions[session_id]
                s.touch()
                return s
            s = Session(session_id or str(uuid.uuid4()))
            self._sessions[s.session_id] = s
        logger.info(f"Session get_or_create: {s.session_id}")
        return s

    def cleanup_expired(self):
        """Remove sessions older than TTL."""
        now = time.time()
        with self._lock:
            expired = [sid for sid, s in self._sessions.items()
                       if now - s.last_active > _SESSION_TTL]
            for sid in expired:
                s = self._sessions.pop(sid)
                logger.info(f"Session expired: {sid}")
                # Clean up session files
                import shutil
                d = os.path.join(_TEMP_DIR, sid)
                if os.path.isdir(d):
                    shutil.rmtree(d, ignore_errors=True)

    def all_ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions.keys())


# Module-level singleton
_store = SessionStore()


def get_store() -> SessionStore:
    return _store
