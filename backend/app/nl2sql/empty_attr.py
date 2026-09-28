"""Empty-result attribution (PLAN §4.2 ⑥-5, W3).

When a valid SQL returns 0 rows, distinguish:
- suspicious_filter — a WHERE value doesn't exist in the column's distinct
  values (typo / wrong entity). Evidence = nearest real value (difflib).
  Pipeline feeds this into one targeted repair round.
- truly_no_data — all filter values exist; base table row count proves the
  data simply isn't there. Answer honestly, no fabrication.
- unknown — no comparable string filters (ranges/aggregates/HAVING).

Deterministic first (zero LLM cost): sqlglot parses WHERE; live distinct
values corroborate. LLM repair reuses the existing nl2sql.generate loop.
"""

from __future__ import annotations

import difflib
from difflib import SequenceMatcher

from ..db.session import get_conn


def _distinct_values(table: str, column: str, limit: int = 1000) -> list[str]:
    with get_conn(readonly=True) as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL LIMIT %s', (limit,))
        return [str(r[0]) for r in cur.fetchall()]


def _table_row_count(table: str) -> int | None:
    try:
        with get_conn(readonly=True) as conn, conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            return int(cur.fetchone()[0])
    except Exception:  # noqa: BLE001 — evidence is best-effort
        return None


def _nearest(value: str, candidates: list[str]) -> str | None:
    match = difflib.get_close_matches(value, candidates, n=1, cutoff=0.6)
    if match:
        return match[0]
    best, best_ratio = None, 0.0
    for c in candidates:
        r = SequenceMatcher(None, value.lower(), c.lower()).ratio()
        if r > best_ratio:
            best, best_ratio = c, r
    return best if best_ratio >= 0.5 else None


def extract_string_filters(sql: str) -> list[dict]:
    """[{table, column, value}] from WHERE equality / IN / LIKE string literals.

    Returns [] on parse failure or when no string filters exist. Read-only,
    deterministic, cheap — designed to run on every empty result.
    """
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(sql, dialect="postgres")
    except Exception:  # noqa: BLE001
        return []

    # alias → real table name
    alias_map: dict[str, str] = {}
    for t in tree.find_all(exp.Table):
        if t.alias:
            alias_map[str(t.alias)] = t.name

    where = tree.find(exp.Where)
    if where is None:
        return []

    filters: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def _col_of(node) -> tuple[str, str] | None:
        """(table_or_alias, column) from a Column node; None otherwise."""
        if isinstance(node, exp.Column):
            tbl = str(node.table) if node.table else ""
            return tbl, node.name
        return None

    def _literal_str(node) -> str | None:
        if isinstance(node, exp.Literal) and node.is_string:
            return str(node.this)
        return None

    for cond in where.find_all(exp.EQ):
        col = _col_of(cond.left) or _col_of(cond.right)
        val = _literal_str(cond.right) or _literal_str(cond.left)
        if col and val:
            key = (col[0], col[1], val)
            if key not in seen:
                seen.add(key)
                filters.append({"table_or_alias": col[0], "column": col[1], "value": val})

    for cond in where.find_all(exp.Like):
        col = _col_of(cond.this)
        val = _literal_str(cond.expression)
        if col and val:
            bare = val.replace("%", "").strip()
            if bare:
                key = (col[0], col[1], bare)
                if key not in seen:
                    seen.add(key)
                    filters.append({"table_or_alias": col[0], "column": col[1],
                                    "value": bare, "like": True})

    for cond in where.find_all(exp.In):
        col = _col_of(cond.this)
        if not col:
            continue
        for e in cond.expressions:
            val = _literal_str(e)
            if val:
                key = (col[0], col[1], val)
                if key not in seen:
                    seen.add(key)
                    filters.append({"table_or_alias": col[0], "column": col[1], "value": val})

    return filters


def attribute_empty_sql(sql: str) -> dict:
    """Classify an empty result. See module docstring for the verdicts."""
    from ..db import schema_meta

    filters = extract_string_filters(sql)
    if not filters:
        return {"verdict": "unknown", "evidence_note": "",
                "note": "无可比对的字符串过滤条件（范围/聚合/HAVING），归因不确定"}

    tables = {t.name: t for t in schema_meta.load_table_meta()}

    # resolve alias/unqualified columns to real tables (only FROM-side tables)
    from_tables = _from_tables(sql)
    alias_real = _alias_pairs(sql)
    resolved: list[dict] = []
    for f in filters:
        tbl = f["table_or_alias"]
        if tbl in alias_real:
            real = alias_real[tbl]
        elif tbl in tables:
            real = tbl
        else:
            real = None
            for cand in from_tables:
                tmeta = tables.get(cand)
                if tmeta and any(c.name == f["column"] for c in tmeta.columns):
                    real = cand
                    break
        if real is None:
            return {"verdict": "unknown", "evidence_note": "",
                    "note": f"无法定位列 {f['table_or_alias']}.{f['column']} 所属表"}
        resolved.append({**f, "table": real})

    # value-existence check (the core evidence)
    for f in resolved:
        values = _distinct_values(f["table"], f["column"])
        if f["value"] in values:
            continue
        near = _nearest(f["value"], values)
        note = (f"过滤值 '{f['value']}' 在 {f['table']}.{f['column']} 的取值中不存在"
                + (f"，最接近的是 '{near}'" if near else ""))
        return {"verdict": "suspicious_filter", "evidence_note": note,
                "column": f"{f['table']}.{f['column']}", "bad_value": f["value"],
                "nearest_value": near}

    # all values exist → base-table count corroborates 真无数据
    first_table = resolved[0]["table"]
    n = _table_row_count(first_table)
    return {"verdict": "truly_no_data",
            "evidence_note": f"基表 {first_table} 共 {n} 行，过滤值均真实存在，确无匹配数据",
            "base_table": first_table, "base_rows": n}


def _from_tables(sql: str) -> list[str]:
    import sqlglot
    from sqlglot import exp
    try:
        tree = sqlglot.parse_one(sql, dialect="postgres")
    except Exception:  # noqa: BLE001
        return []
    return [t.name for t in tree.find_all(exp.Table)]


def _alias_pairs(sql: str) -> dict[str, str]:
    import sqlglot
    from sqlglot import exp
    try:
        tree = sqlglot.parse_one(sql, dialect="postgres")
    except Exception:  # noqa: BLE001
        return {}
    return {str(t.alias): t.name for t in tree.find_all(exp.Table) if t.alias}
