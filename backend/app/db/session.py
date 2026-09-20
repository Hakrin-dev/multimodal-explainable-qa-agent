"""PostgreSQL access helpers (psycopg3). Business DB = Chinook."""

from __future__ import annotations

import threading
from contextlib import contextmanager

import psycopg

from ..core.config import get_settings

_lock = threading.Lock()
_cached_dsn: str | None = None


def _dsn() -> str:
    global _cached_dsn
    with _lock:
        if _cached_dsn is None:
            _cached_dsn = get_settings().database_url
        return _cached_dsn


@contextmanager
def get_conn(readonly: bool = True, autocommit: bool = True):
    """Connection factory. NL2SQL executions must use readonly=True."""
    with psycopg.connect(_dsn(), autocommit=autocommit) as conn:
        if readonly:
            conn.read_only = True
        yield conn


def test_connection() -> bool:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone() is not None
    except Exception:
        return False
