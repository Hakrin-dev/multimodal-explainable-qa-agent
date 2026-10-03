"""PostgreSQL access helpers (psycopg3). Business DB = Chinook."""

from __future__ import annotations

import threading
import time
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
    """Connection factory. NL2SQL executions must use readonly=True.

    Connection establishment is retried briefly: transient DNS / port-forward
    hiccups (e.g. a container port-proxy flap under sustained load) must not
    fail an entire eval case or turn. No-op on a healthy network.
    """
    conn = None
    last: Exception | None = None
    for attempt in range(5):
        try:
            conn = psycopg.connect(_dsn(), autocommit=autocommit)
            break
        except psycopg.OperationalError as exc:
            last = exc
            if attempt < 4:
                time.sleep(2 * (attempt + 1))
    if conn is None:
        raise last if last else psycopg.OperationalError("db connect failed")
    try:
        if readonly:
            conn.read_only = True
        yield conn
    finally:
        conn.close()


def test_connection() -> bool:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone() is not None
    except Exception:
        return False
