"""Read-only SQL execution with timeout & row budget (PLAN §4.2 step ⑥)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..db.session import get_conn


@dataclass
class ExecutionResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    error: str = ""
    latency_ms: int = 0


# belt & suspenders next to the read-only connection: reject obvious writes
_WRITE_PATTERN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy)\b",
    re.IGNORECASE,
)


def execute_sql(sql: str, max_rows: int = 50, timeout_ms: int = 5000) -> ExecutionResult:
    if _WRITE_PATTERN.search(sql):
        return ExecutionResult(False, error="rejected: write-like statement")

    import time

    t0 = time.monotonic()
    try:
        with get_conn(readonly=True) as conn, conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {int(timeout_ms)}")
            cur.execute(sql)
            if cur.description is None:
                return ExecutionResult(False, error="statement returned no result set")
            columns = [d.name for d in cur.description]
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            rows = rows[:max_rows]
            return ExecutionResult(
                ok=True, columns=columns, rows=rows, row_count=len(rows),
                truncated=truncated,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
    except Exception as e:  # noqa: BLE001
        return ExecutionResult(False, error=str(e).strip(),
                               latency_ms=int((time.monotonic() - t0) * 1000))
