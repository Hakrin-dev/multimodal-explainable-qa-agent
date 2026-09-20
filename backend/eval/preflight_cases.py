"""Case preflight: validate eval case files before they enter regression.

Checks per case:
  1. reference_sql executes against the live DB
  2. result rows <= --max-rows (the eval matcher budget; large sets break
     execution-accuracy comparison because candidate SQL is LIMIT-clamped)
  3. reference_sql passes our own SQLValidator (sqlglot AST + schema check)
  4. runs twice -> identical output (determinism guard for ordered matching)

Usage: python eval/preflight_cases.py [cases.jsonl ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.db import schema_meta  # noqa: E402
from app.db.session import get_conn  # noqa: E402
from app.nl2sql.validator import SQLValidator  # noqa: E402


def fetch(sql: str, cap: int) -> list[tuple]:
    with get_conn(readonly=True) as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [tuple(r) for r in cur.fetchmany(cap)]


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--max-rows", type=int, default=200)
    args = ap.parse_args()

    tables = schema_meta.load_table_meta()
    schema = {t.name: {c.name: c.type for c in t.columns} for t in tables}
    validator = SQLValidator(schema, max_rows=get_settings().sql_max_rows)

    failed = 0
    for f in args.files:
        path = Path(f) if Path(f).is_absolute() else BACKEND / "eval/cases" / f
        cases = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"\n== {path.name}  ({len(cases)} cases)")
        import json

        for line in cases:
            case = json.loads(line)
            problems: list[str] = []
            try:
                rows = fetch(case["reference_sql"], args.max_rows + 1)
                if len(rows) > args.max_rows:
                    problems.append(f"result rows {len(rows)} > budget {args.max_rows}")
                if len(rows) == 0:
                    problems.append("empty result set (weak case / wrong filter?)")
                again = fetch(case["reference_sql"], args.max_rows + 1)
                if rows != again:
                    problems.append("non-deterministic result (missing ORDER BY tiebreaker?)")
                vres = validator.validate(case["reference_sql"])
                if not vres.ok:
                    problems.append(f"validator: {'; '.join(vres.errors)[:120]}")
            except Exception as e:  # noqa: BLE001
                problems.append(f"EXECUTION ERROR: {e}")
            if problems:
                failed += 1
                print(f"  ✗ {case['id']}")
                for p in problems:
                    print(f"      {p}")
            else:
                print(f"  ✓ {case['id']}  rows={len(rows)}")

    print(f"\n{'✗ FAILED' if failed else '✓ all good'}: {failed} problem case(s)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
