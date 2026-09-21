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


def run_reference(sql: str, max_rows: int = 200) -> tuple[list[tuple], list[str]]:
    with get_conn(readonly=True) as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [d.name for d in cur.description]
        return [tuple(r) for r in cur.fetchmany(max_rows)], cols


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


# ---------------- four-level semantic execution-accuracy matcher ----------------
# L1 ordered-exact    — strictest; rows as ordered tuples (top-N questions)
# L2 multiset-exact   — same rows, any order ("有哪些"-style questions)
# L3 name-mapped      — candidate may add/reorder columns; ref cols matched by name
# L4 cell-containment — ref row cells ⊆ candidate row cells (extra cols tolerated)
# Models legitimately differ in projection shape (id vs name, extra id col);
# L1-only scoring punishes good answers. Report both strict & semantic rates.


def _rows_norm(rows: list[tuple]) -> list[tuple]:
    return [tuple(normalize(c) for c in r) for r in rows]


def _multiset_match(a: list[tuple], b: list[tuple]) -> bool:
    if len(a) != len(b):
        return False
    return sorted(a, key=repr) == sorted(b, key=repr)


def _name_mapped_match(cand_rows, cand_cols, ref_rows, ref_cols) -> bool:
    """Map candidate columns onto reference columns by name (case-insens)."""
    idx = {c.lower(): i for i, c in enumerate(cand_cols)}
    if any(c.lower() not in idx for c in ref_cols):
        return False
    remapped = [tuple(r[idx[c.lower()]] for c in ref_cols) for r in cand_rows]
    return _multiset_match(remapped, ref_rows)


def _containment_match(cand_rows: list[tuple], ref_rows: list[tuple]) -> bool:
    """Each ref row's cell multiset ⊆ some candidate row's cell multiset,
    with a consistent injective mapping."""
    from collections import Counter
    if len(cand_rows) != len(ref_rows):
        return False
    cand_sets = [Counter(r) for r in cand_rows]
    used = [False] * len(cand_sets)
    for ref in ref_rows:
        need = Counter(ref)
        found = -1
        for i, cs in enumerate(cand_sets):
            if used[i]:
                continue
            if all(cs.get(k, 0) >= v for k, v in need.items()):
                found = i
                break
        if found < 0:
            return False
        used[found] = True
    return True


def result_match(cand_rows: list[tuple], ref_rows: list[tuple],
                 cand_cols: list[str] | None = None,
                 ref_cols: list[str] | None = None) -> bool:
    """Semantic execution accuracy (any level passes)."""
    if len(cand_rows) != len(ref_rows):
        return False
    a, b = _rows_norm(cand_rows), _rows_norm(ref_rows)
    if not b:  # both empty
        return True
    if a == b:  # L1 ordered
        return True
    if _multiset_match(a, b):  # L2
        return True
    if cand_cols and ref_cols and _name_mapped_match(a, cand_cols, b, ref_cols):  # L3
        return True
    return _containment_match(a, b)  # L4


def result_match_level(cand_rows, ref_rows, cand_cols=None, ref_cols=None) -> int:
    """0 = fail, 1..4 = pass at level (diagnostics)."""
    if len(cand_rows) != len(ref_rows):
        return 0
    a, b = _rows_norm(cand_rows), _rows_norm(ref_rows)
    if not b:
        return 1
    if a == b:
        return 1
    if _multiset_match(a, b):
        return 2
    if cand_cols and ref_cols and _name_mapped_match(a, cand_cols, b, ref_cols):
        return 3
    return 4 if _containment_match(a, b) else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="+",
                    default=[str(Path(__file__).parent / "cases/nl2sql_single_table.jsonl")])
    ap.add_argument("--only-ids", default="",
                    help="comma-separated case id filter (for L0 smoke subsets)")
    args = ap.parse_args()

    s = get_settings()
    only = {x.strip() for x in args.only_ids.split(",") if x.strip()}
    cases: list[dict] = []
    for path in args.cases:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                c = json.loads(line)
                if not only or c["id"] in only:
                    cases.append(c)
    print(f"provider={s.llm_provider} model={s.active_model} cases={len(cases)}\n")

    pipeline = NL2SQLPipeline()
    results = []
    for case in cases:
        t0 = time.monotonic()
        try:
            r = pipeline.run(case["question"])
            cand = [tuple(row) for row in r.rows[:200]]
            ref, ref_cols = run_reference(case["reference_sql"])
            level = result_match_level(cand, ref, r.columns, ref_cols)
            passed = r.status == "ok" and level > 0
            strict = r.status == "ok" and level == 1
            results.append({
                "id": case["id"], "passed": passed, "strict": strict,
                "level": level,
                "first_pass": passed and r.repair_rounds == 0,
                "status": r.status, "repair_rounds": r.repair_rounds,
                "latency_ms": r.latency_ms, "sql": r.sql,
            })
            mark = "✓" if passed else "✗"
            lvl = {1: "", 2: " (无序等价)", 3: " (列名映射)", 4: " (含冗余列)"}.get(level, "")
            extra = f" [{r.status}]" if r.status != "ok" else ""
            print(f"  {mark} {case['id']}  {r.latency_ms:>5}ms  repairs={r.repair_rounds}{extra}{lvl}")
            if not passed:
                print(f"      sql: {r.sql[:160]}")
                if r.errors:
                    print(f"      err: {r.errors[0][:160]}")
        except Exception as e:  # noqa: BLE001
            results.append({"id": case["id"], "passed": False, "first_pass": False,
                            "error": str(e)})
            print(f"  ✗ {case['id']}  EXCEPTION {e}")

    if not results:
        print("no cases matched — check --only-ids")
        sys.exit(1)

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
