"""kb_formula persistence — Schema frozen per ingestion_ir.md §3.

DDL mirrors FormulaIR (app/ingestion/ir.py). Created at first access like
kb_doc/kb_chunk (rag/store.py pattern) and biz_term — no separate migration.
"""

from __future__ import annotations

import json
from typing import Any

from ..db.session import get_conn
from ..ingestion.ir import FormulaIR

_DDL = """
CREATE TABLE IF NOT EXISTS kb_formula (
    id         TEXT PRIMARY KEY,
    doc_id     TEXT NOT NULL,
    page       INT  NOT NULL,
    breadcrumb TEXT[] NOT NULL DEFAULT '{}',
    name       TEXT NOT NULL DEFAULT '',
    latex      TEXT NOT NULL,
    params     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kb_formula_doc_idx ON kb_formula (doc_id);
"""


def ensure_schema() -> None:
    with get_conn(readonly=False) as conn, conn.cursor() as cur:
        cur.execute(_DDL)


def upsert(f: FormulaIR) -> None:
    """Idempotent insert/replace by formula id."""
    ensure_schema()
    with get_conn(readonly=False) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO kb_formula (id, doc_id, page, breadcrumb, name, latex, params)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET
                 doc_id=EXCLUDED.doc_id, page=EXCLUDED.page,
                 breadcrumb=EXCLUDED.breadcrumb, name=EXCLUDED.name,
                 latex=EXCLUDED.latex, params=EXCLUDED.params""",
            (f.id, f.doc_id, f.page, list(f.breadcrumb), f.name, f.latex,
             json.dumps(f.params, ensure_ascii=False)),
        )


def get(formula_id: str) -> FormulaIR | None:
    ensure_schema()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, doc_id, page, breadcrumb, name, latex, params"
                    " FROM kb_formula WHERE id = %s", (formula_id,))
        row = cur.fetchone()
    return _row_to_ir(row) if row else None


def list_all() -> list[FormulaIR]:
    ensure_schema()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, doc_id, page, breadcrumb, name, latex, params"
                    " FROM kb_formula ORDER BY id")
        return [_row_to_ir(r) for r in cur.fetchall()]


def _replace_for_doc(
    cursor: Any,
    doc_id: str,
    formulas: list[FormulaIR],
) -> None:
    cursor.execute(
        "DELETE FROM kb_formula WHERE doc_id = %s",
        (doc_id,),
    )

    for formula in formulas:
        cursor.execute(
            """INSERT INTO kb_formula
               (id, doc_id, page, breadcrumb, name, latex, params)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                formula.id,
                formula.doc_id,
                formula.page,
                list(formula.breadcrumb),
                formula.name,
                formula.latex,
                json.dumps(
                    formula.params,
                    ensure_ascii=False,
                ),
            ),
        )


def replace_for_doc(
    doc_id: str,
    formulas: list[FormulaIR],
    *,
    cursor: Any | None = None,
) -> None:
    """Atomically replace every registered formula for one document."""

    rows = list(formulas)
    mismatched = [
        formula.id
        for formula in rows
        if formula.doc_id != doc_id
    ]
    if mismatched:
        raise ValueError(
            f"formula doc_id mismatch for {mismatched}"
        )

    if cursor is not None:
        _replace_for_doc(cursor, doc_id, rows)
        return

    ensure_schema()
    with get_conn(readonly=False) as conn, conn.cursor() as cur:
        with conn.transaction():
            _replace_for_doc(cur, doc_id, rows)


def list_for_doc(doc_id: str) -> list[FormulaIR]:
    ensure_schema()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, doc_id, page, breadcrumb, name, latex, params"
            " FROM kb_formula WHERE doc_id = %s ORDER BY id",
            (doc_id,),
        )
        return [_row_to_ir(row) for row in cur.fetchall()]


def _row_to_ir(row: tuple) -> FormulaIR:
    fid, doc_id, page, breadcrumb, name, latex, params = row
    if isinstance(params, str):
        params = json.loads(params)
    return FormulaIR(
        id=fid, doc_id=doc_id, page=page,
        breadcrumb=list(breadcrumb or []), name=name or "",
        latex=latex, params=dict(params or {}),
    )
