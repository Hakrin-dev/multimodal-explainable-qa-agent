"""Session memory (PLAN §4.6): multi-turn history + clarify suspension.

v1: in-process store (dict + TTL). W2: persist to PG (trace_event &
session tables) — the interface (messages in, messages out) stays.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    session_id: str
    messages: list[dict] = field(default_factory=list)   # {"role","content"}
    pending_clarify: dict | None = None                   # suspended ask_user
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)


class SessionStore:
    """In-memory sessions with TTL eviction. Thread-safe."""

    def __init__(self, ttl_seconds: int = 3600 * 12, max_sessions: int = 500):
        self.ttl = ttl_seconds
        self.max_sessions = max_sessions
        self._lock = threading.RLock()   # reentrant: set_pending/append call get()
        self._sessions: dict[str, Session] = {}

    def get(self, session_id: str) -> Session:
        with self._lock:
            self._evict()
            s = self._sessions.get(session_id)
            if s is None:
                s = Session(session_id=session_id)
                self._sessions[session_id] = s
            s.last_active = time.time()
            return s

    def history(self, session_id: str, limit: int = 6) -> list[dict]:
        """Last `limit` messages (for intent / rewrite context)."""
        return list(self.get(session_id).messages[-limit:])

    def append(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            s = self.get(session_id)
            s.messages.append({"role": role, "content": content})
            if len(s.messages) > 40:
                s.messages = s.messages[-40:]

    def set_pending_clarify(self, session_id: str, payload: dict | None) -> None:
        with self._lock:
            self.get(session_id).pending_clarify = payload

    def pop_pending_clarify(self, session_id: str) -> dict | None:
        with self._lock:
            s = self.get(session_id)
            p = s.pending_clarify
            s.pending_clarify = None
            return p

    def _evict(self) -> None:
        now = time.time()
        stale = [sid for sid, s in self._sessions.items()
                 if now - s.last_active > self.ttl]
        for sid in stale:
            del self._sessions[sid]
        if len(self._sessions) > self.max_sessions:
            for sid in sorted(self._sessions, key=lambda x: self._sessions[x].last_active):
                del self._sessions[sid]
                if len(self._sessions) <= self.max_sessions:
                    break


_store: SessionStore | None = None
_store_lock = threading.Lock()


def get_session_store() -> SessionStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = SessionStore()
        return _store
