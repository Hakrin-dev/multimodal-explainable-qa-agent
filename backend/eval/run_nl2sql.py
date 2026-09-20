"""Minimal NL2SQL eval runner (L0 smoke seed; full graded system is C's W3 deliverable).

Usage:
    python eval/run_nl2sql.py [--cases eval/cases/nl2sql_single_table.jsonl]

Scoring: execution accuracy — candidate SQL result set vs reference SQL
result set (ordered compare, numeric tolerance 1e-4). Repair-loop counts as
success (it is part of the system), first-pass rate reported separately.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings, var_dir  # noqa: E402
from app.db.session import get_conn  # noqa: E402
from app.nl2sql.pipeline import NL2SQLPipeline  # noqa: E402


def run_reference(sql: str, max_rows: int = 200) -> list[tuple]:
    with get_conn(readonly=True) as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [d.name for d in cur.description]
        return [tuple(r) for r in cur.fetchmany(max_rows)]


def normalize(cell):
    if cell is None:
        return None
    if isinstance(cell, (int, float)):
        return round(float(cell), 4)
    if isinstance(cell, dt.datetime):
        return cell.isoformat()
    s = str(cell).strip()
    try:
        return round(float(s), 4)
    except ValueError:
        return s


def result_match(a: list[tuple], b: list[tuple]) -> bool:
    if len(a) != len(b):
        return False
    return all(tuple(normalize(c) for c in ra) == tuple(normalize(c) for c in rb)
               for ra, rb in zip(a, b))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(Path(__file__).parent / "cases/nl2sql_single_table.jsonl"))
    args = ap.parse_args()

    s = get_settings()
    cases = [json.loads(l) for l in Path(args.cases).read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"provider={s.llm_provider} model={s.active_model} cases={len(cases)}\n")

    pipeline = NL2SQLPipeline()
    results = []
    for case in cases:
        t0 = time.monotonic()
        try:
            r = pipeline.run(case["question"])
            cand = [tuple(row) for row in r.rows[:200]]
            ref = run_reference(case["reference_sql"])
            passed = r.status == "ok" and result_match(cand, ref)
            results.append({
                "id": case["id"], "passed": passed,
                "first_pass": passed and r.repair_rounds == 0,
                "status": r.status, "repair_rounds": r.repair_rounds,
                "latency_ms": r.latency_ms, "sql": r.sql,
            })
            mark = "✓" if passed else "✗"
            extra = f" [{r.status}]" if r.status != "ok" else ""
            print(f"  {mark} {case['id']}  {r.latency_ms:>5}ms  repairs={r.repair_rounds}{extra}")
            if not passed:
                print(f"      sql: {r.sql[:160]}")
                if r.errors:
                    print(f"      err: {r.errors[0][:160]}")
        except Exception as e:  # noqa: BLE001
            results.append({"id": case["id"], "passed": False, "first_pass": False,
                            "error": str(e)})
            print(f"  ✗ {case['id']}  EXCEPTION {e}")

    n = len(results)
    acc = sum(r["passed"] for r in results) / n
    fp = sum(r.get("first_pass", False) for r in results) / n
    print(f"\nexecution accuracy: {acc:.0%} ({sum(r['passed'] for r in results)}/{n})"
          f"   first-pass: {fp:.0%}")

    out = var_dir() / "eval" / f"nl2sql_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "provider": s.llm_provider, "model": s.active_model,
        "accuracy": acc, "first_pass": fp, "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {out.relative_to(BACKEND.parent)}")

    # LLM cost of this run
    from app.core.llm import get_llm_service
    summary = get_llm_service().usage_summary()
    print(f"llm cost so far: ¥{summary['total_cost_rmb']}")


if __name__ == "__main__":
    main()
