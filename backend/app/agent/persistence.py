"""Turn & session persistence (PLAN §4.6, kernel W2 item).

Tables:
  trace_turn   — one row per turn (question/status/cost summary)
  trace_event  — one row per TraceNode (flat form, contract trace.md §4.3)
  app_session  — messages + pending clarify (durability across restarts)

All writes are best-effort: PG is an enhancement (replay/history), the
in-process state remains the hot path. Failures degrade silently to
memory-only so the kernel never breaks on DB hiccups.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from ..core.tracing import TraceCollector
from ..db.session import get_conn

_DDL = """
CREATE TABLE IF NOT EXISTS trace_turn (
    turn_id    TEXT PRIMARY KEY,
    session_id TEXT,
    question   TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    summary    JSONB NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS trace_event (
    turn_id    TEXT NOT NULL,
    seq        INT NOT NULL,
    session_id TEXT,
    node       JSONB NOT NULL,
    PRIMARY KEY (turn_id, seq)
);
CREATE INDEX IF NOT EXISTS trace_event_session_idx ON trace_event (session_id, seq);
CREATE TABLE IF NOT EXISTS app_session (
    session_id      TEXT PRIMARY KEY,
    messages        JSONB NOT NULL DEFAULT '[]',
    pending_clarify JSONB,
    created_at      TEXT NOT NULL DEFAULT '',
    last_active     TEXT NOT NULL DEFAULT '',
    n_turns         INT NOT NULL DEFAULT 0
);
"""

_lock = threading.Lock()
_ready = False


def ensure_tables() -> bool:
    global _ready
    with _lock:
        if _ready:
            return True
        try:
            with get_conn(readonly=False) as conn, conn.cursor() as cur:
                cur.execute(_DDL)
            _ready = True
            return True
        except Exception:
            return False


# ------------------------------------------------------------------ turns --

def save_turn(trace: TraceCollector, session_id: str,
              summary: dict[str, Any]) -> bool:
    """Persist one completed turn: trace_turn row + flat trace_event rows."""
    if not ensure_tables():
        return False
    try:
        tree = trace.to_dict()
        root = tree["root"]
        flat = _preorder(root)
        with get_conn(readonly=False) as conn, conn.cursor() as cur:
            with conn.transaction():
                cur.execute(
                    "INSERT INTO trace_turn (turn_id, session_id, question, created_at, summary)"
                    " VALUES (%s,%s,%s,%s,%s) ON CONFLICT (turn_id) DO UPDATE SET summary=%s",
                    (tree["turn_id"], session_id, tree["question"],
                     tree["created_at"], json.dumps(summary, ensure_ascii=False, default=str),
                     json.dumps(summary, ensure_ascii=False, default=str)))
                for seq, node in enumerate(flat):
                    cur.execute(
                        "INSERT INTO trace_event (turn_id, seq, session_id, node)"
                        " VALUES (%s,%s,%s,%s) ON CONFLICT (turn_id, seq) DO NOTHING",
                        (tree["turn_id"], seq, session_id,
                         json.dumps(node, ensure_ascii=False, default=str)))
        return True
    except Exception:
        return False


def _from_jsonb(val):
    """psycopg auto-parses JSONB to dict/list; tolerate both shapes."""
    if val is None or isinstance(val, (dict, list)):
        return val
    return json.loads(val)


def load_trace(turn_id: str) -> dict[str, Any] | None:
    """Rebuild the full trace tree for /api/trace/{turn_id}."""
    if not ensure_tables():
        return None
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT question, created_at, summary FROM trace_turn"
                        " WHERE turn_id = %s", (turn_id,))
            row = cur.fetchone()
            if not row:
                return None
            question, created_at, summary = row
            cur.execute("SELECT node FROM trace_event WHERE turn_id = %s"
                        " ORDER BY seq", (turn_id,))
            nodes = [_from_jsonb(r[0]) for r in cur.fetchall()]
        root = _rebuild_tree(turn_id, nodes)
        return {
            "trace_version": "0.1", "turn_id": turn_id, "question": question,
            "created_at": created_at, "summary": summary, "root": root,
        }
    except Exception:
        return None


def list_turns(session_id: str | None = None, limit: int = 20) -> list[dict]:
    if not ensure_tables():
        return []
    try:
        with get_conn() as conn, conn.cursor() as cur:
            if session_id:
                cur.execute("SELECT turn_id, session_id, question, created_at, summary"
                            " FROM trace_turn WHERE session_id = %s"
                            " ORDER BY created_at DESC LIMIT %s", (session_id, limit))
            else:
                cur.execute("SELECT turn_id, session_id, question, created_at, summary"
                            " FROM trace_turn ORDER BY created_at DESC LIMIT %s", (limit,))
            return [{"turn_id": r[0], "session_id": r[1], "question": r[2],
                     "created_at": r[3], "summary": _from_jsonb(r[4])} for r in cur.fetchall()]
    except Exception:
        return []


# --------------------------------------------------------------- sessions --

def save_session(session_id: str, messages: list[dict],
                 pending_clarify: dict | None, n_turns: int) -> bool:
    if not ensure_tables():
        return False
    try:
        import datetime as dt
        now = dt.datetime.now().isoformat(timespec="seconds")
        with get_conn(readonly=False) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app_session (session_id, messages, pending_clarify,"
                " created_at, last_active, n_turns) VALUES (%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (session_id) DO UPDATE SET messages=%s,"
                " pending_clarify=%s, last_active=%s, n_turns=%s",
                (session_id, json.dumps(messages, ensure_ascii=False),
                 json.dumps(pending_clarify, ensure_ascii=False) if pending_clarify else None,
                 now, now, n_turns,
                 json.dumps(messages, ensure_ascii=False),
                 json.dumps(pending_clarify, ensure_ascii=False) if pending_clarify else None,
                 now, n_turns))
        return True
    except Exception:
        return False


def load_session(session_id: str) -> dict | None:
    if not ensure_tables():
        return None
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT messages, pending_clarify FROM app_session"
                        " WHERE session_id = %s", (session_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {"messages": _from_jsonb(row[0]) or [],
                    "pending_clarify": _from_jsonb(row[1])}
    except Exception:
        return None


# ---------------------------------------------------------------- helpers --

def _preorder(root: dict) -> list[dict]:
    """Parents before children (topological), preserving sibling order."""
    out: list[dict] = []

    def walk(node: dict) -> None:
        out.append(node)
        for c in node.get("children", []):
            walk(c)

    walk(root)
    return out


def _rebuild_tree(turn_id: str, nodes: list[dict]) -> dict | None:
    by_id = {n["id"]: dict(n, children=[]) for n in nodes}
    root = by_id.get(turn_id)
    if root is None and nodes:  # tolerate renamed roots: first parentless node
        root = next((n for n in by_id.values() if n["parent_id"] is None), None)
    if root is None:
        return None
    for n in by_id.values():
        parent = by_id.get(n["parent_id"]) if n["parent_id"] else None
        if parent is not None and n is not root:
            parent["children"].append(n)
    return root
