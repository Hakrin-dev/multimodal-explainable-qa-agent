"""W3 ablation: L1 accuracy + linking/rerank compression stats in one pass.

Usage:
    python eval/ablate_w3.py [--cases ...] [--rerank 1|0] [--tag w3_rerank_on]

Collects per case: pass/level/first_pass + schema_context trace detail
(selected_tables, recalled, rerank before/after, tokens full/compressed).
Output: var/eval/ablate_w3_<tag>.json
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
from eval.run_nl2sql import result_match_level, run_reference  # noqa: E402
from app.nl2sql.pipeline import NL2SQLPipeline  # noqa: E402


def find_trace_nodes(node: dict, label: str, out: list[dict]) -> None:
    if node.get("label") == label:
        out.append(node)
    for c in node.get("children", []):
        find_trace_nodes(c, label, out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="+", default=[
        str(Path(__file__).parent / "cases/nl2sql_single_table.jsonl"),
        str(Path(__file__).parent / "cases/nl2sql_multi_table.jsonl"),
    ])
    ap.add_argument("--rerank", default="1", choices=["0", "1"])
    ap.add_argument("--tag", default="w3")
    args = ap.parse_args()

    s = get_settings()
    s.schema_linking = True
    s.schema_linking_rerank = args.rerank == "1"

    cases: list[dict] = []
    for path in args.cases:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                cases.append(json.loads(line))

    print(f"provider={s.llm_provider} model={s.active_model} cases={len(cases)} "
          f"linking=1 rerank={s.schema_linking_rerank}\n")

    pipeline = NL2SQLPipeline()
    results = []

    def run_case(case):
        """Run one case; retry on infra-level connection errors (WSL2
        docker-proxy intermittently drops the 5433 relay under sustained
        load — app-level backoff rides out the window)."""
        import psycopg
        last = None
        for attempt in range(4):
            try:
                return pipeline.run(case["question"])
            except psycopg.OperationalError as e:
                last = e
                print(f"    [infra] conn flake on {case['id']}, backoff 15s "
                      f"(attempt {attempt + 1}/4)")
                time.sleep(15)
        raise last

    for case in cases:
        t0 = time.monotonic()
        try:
            r = run_case(case)
            cand = [tuple(row) for row in r.rows[:200]]
            ref, ref_cols = run_reference(case["reference_sql"])
            level = result_match_level(cand, ref, r.columns, ref_cols)
            passed = r.status == "ok" and level > 0

            nodes: list[dict] = []
            find_trace_nodes(r.trace.get("root", {}), "schema_context", nodes)
            sc = nodes[0].get("detail", {}) if nodes else {}
            rr_nodes: list[dict] = []
            find_trace_nodes(r.trace.get("root", {}), "link_rerank", rr_nodes)
            rr = rr_nodes[0].get("detail", {}) if rr_nodes else {}
            empty_nodes: list[dict] = []
            find_trace_nodes(r.trace.get("root", {}), "empty_attr", empty_nodes)

            results.append({
                "id": case["id"], "passed": passed, "level": level,
                "first_pass": passed and r.repair_rounds == 0,
                "status": r.status, "repair_rounds": r.repair_rounds,
                "latency_ms": r.latency_ms,
                "tables": sc.get("selected_tables"),
                "rerank_before": rr.get("before"), "rerank_after": rr.get("after"),
                "rerank_dropped": rr.get("dropped"),
                "tokens_full": sc.get("tokens_full"),
                "tokens_compressed": sc.get("tokens_compressed"),
                "empty_verdict": (empty_nodes[0].get("output") if empty_nodes else None),
                "rewrites": _rewrites(r),
            })
            mark = "✓" if passed else "✗"
            n_tab = len(sc.get("selected_tables") or [])
            print(f"  {mark} {case['id']:8s} {r.latency_ms:>6}ms tables={n_tab} "
                  f"tok={sc.get('tokens_compressed')}/{sc.get('tokens_full')} "
                  f"repairs={r.repair_rounds} [{r.status}]")
            if not passed:
                print(f"      sql: {r.sql[:150]}")
        except Exception as e:  # noqa: BLE001
            results.append({"id": case["id"], "passed": False, "error": str(e)})
            print(f"  ✗ {case['id']}  EXCEPTION {e}")

    n = len(results)
    ok = [r for r in results if r.get("passed")]
    acc = len(ok) / n if n else 0
    fp = sum(r.get("first_pass", False) for r in results) / n if n else 0

    # compression stats over cases with linking detail
    tab_counts = [len(r["tables"]) for r in results if r.get("tables")]
    tok_pairs = [(r["tokens_compressed"], r["tokens_full"]) for r in results
                 if r.get("tokens_full")]
    rerank_drops = [len(r["rerank_dropped"]) for r in results if r.get("rerank_dropped")]
    reranked_n = sum(1 for r in results if r.get("rerank_dropped"))

    stats = {
        "accuracy": acc, "first_pass": fp, "n": n,
        "avg_tables": sum(tab_counts) / len(tab_counts) if tab_counts else None,
        "avg_tokens_compressed": (sum(a for a, _ in tok_pairs) / len(tok_pairs)
                                  if tok_pairs else None),
        "avg_tokens_full": (sum(b for _, b in tok_pairs) / len(tok_pairs)
                            if tok_pairs else None),
        "token_reduction": (1 - sum(a for a, _ in tok_pairs) / sum(b for _, b in tok_pairs)
                            if tok_pairs else None),
        "rerank_fired": reranked_n,
        "avg_dropped_by_rerank": (sum(rerank_drops) / len(rerank_drops)
                                  if rerank_drops else 0),
    }

    print(f"\naccuracy: {acc:.0%} ({len(ok)}/{n})  first-pass: {fp:.0%}")
    print(f"avg tables: {stats['avg_tables']:.1f}  "
          f"tokens: {stats['avg_tokens_compressed']:.0f}/{stats['avg_tokens_full']:.0f} "
          f"(-{stats['token_reduction']:.0%})  rerank fired: {reranked_n}/{n} "
          f"(avg dropped {stats['avg_dropped_by_rerank']:.1f})")

    out = var_dir() / "eval" / f"ablate_w3_{args.tag}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"config": {"rerank": s.schema_linking_rerank},
                               "stats": stats, "results": results},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {out.relative_to(BACKEND.parent)}")


def _rewrites(r) -> list[dict]:
    nodes: list[dict] = []
    find_trace_nodes(r.trace.get("root", {}), "rewrite", nodes)
    if nodes:
        return nodes[0].get("detail", {}).get("rewrites", [])
    return []


if __name__ == "__main__":
    main()
