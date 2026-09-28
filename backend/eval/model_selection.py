"""W1 LLM model-selection eval (D1 decision, PLAN §10).

DeepSeek-V3 vs Qwen-Plus on 10 NL2SQL single-table cases (st-001..010),
3 repeats each, shared prompts/params. Verdict rule from PLAN:
execution accuracy first; if gap < 5% -> cost (cache-inclusive) then latency.

Usage (needs API keys in .env):
    python eval/model_selection.py [--providers deepseek qwen] [--repeats 3]

Output: var/eval/model_selection_<ts>.md (append to tech-doc appendix).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import Settings, var_dir  # noqa: E402
from app.core.llm import LLMService  # noqa: E402
from app.db.session import get_conn  # noqa: E402
from app.nl2sql.pipeline import NL2SQLPipeline  # noqa: E402

from run_nl2sql import result_match, run_reference  # noqa: E402  (same dir)


def _norm(s) -> str:
    return "".join(str(s).split())


def eval_rag_provider(provider: str, cases: list[dict], repeats: int) -> dict:
    """RAG dimension (PLAN §10): answer_hit + retrieval_hit."""
    settings = Settings()
    settings.llm_provider = provider
    settings.llm_model = ""
    llm = LLMService(settings)
    from app.rag.pipeline import RAGPipeline
    pipeline = RAGPipeline(llm=llm)
    pipeline.ensure_loaded()

    per_case: list[dict] = []
    t_start = time.monotonic()
    for rep in range(1, repeats + 1):
        for case in cases:
            r = pipeline.run(case["question"])
            answer = _norm(r.answer)
            retrieved = _norm("".join(h["text"] for h in r.hits))
            facts = case["expected_facts"]
            per_case.append({
                "case": case["id"], "rep": rep,
                "passed": all(_norm(f) in answer for f in facts),
                "retrieval_hit": all(_norm(f) in retrieved for f in facts),
                "latency_ms": r.latency_ms, "status": r.status,
            })
    wall_s = time.monotonic() - t_start

    acc = sum(c["passed"] for c in per_case) / len(per_case)
    ret = sum(c["retrieval_hit"] for c in per_case) / len(per_case)
    lat = [c["latency_ms"] for c in per_case]
    summary = llm.usage_summary()
    cost = sum(r["cost"] or 0 for r in summary["rows"] if r["provider"] == provider)
    return {
        "provider": provider, "model": settings.provider_config(provider).default_model,
        "accuracy": acc, "retrieval_hit": ret,
        "p50_ms": int(statistics.median(lat)),
        "p95_ms": int(sorted(lat)[int(len(lat) * 0.95) - 1]),
        "cost_rmb": round(cost, 4), "calls": len(per_case),
        "wall_seconds": round(wall_s, 1), "per_case": per_case,
    }


def eval_provider(provider: str, cases: list[dict], repeats: int) -> dict:
    """Run all cases × repeats for one provider; returns aggregated stats."""
    settings = Settings()
    settings.llm_provider = provider
    settings.llm_model = ""  # provider default model
    llm = LLMService(settings)
    pipeline = NL2SQLPipeline(llm=llm)

    per_case: list[dict] = []
    t_start = time.monotonic()
    for rep in range(1, repeats + 1):
        for case in cases:
            r = pipeline.run(case["question"])
            cand = [tuple(row) for row in r.rows[:200]]
            ref, ref_cols = run_reference(case["reference_sql"])
            passed = r.status == "ok" and result_match(cand, ref, r.columns, ref_cols)
            per_case.append({
                "case": case["id"], "rep": rep, "passed": passed,
                "first_pass": passed and r.repair_rounds == 0,
                "latency_ms": r.latency_ms, "repair_rounds": r.repair_rounds,
                "sql": r.sql,
            })
    wall_s = time.monotonic() - t_start

    acc = sum(c["passed"] for c in per_case) / len(per_case)
    fp = sum(c["first_pass"] for c in per_case) / len(per_case)
    lat = [c["latency_ms"] for c in per_case]
    summary = llm.usage_summary()
    cost = sum(r["cost"] or 0 for r in summary["rows"] if r["provider"] == provider)
    return {
        "provider": provider, "model": settings.provider_config(provider).default_model,
        "accuracy": acc, "first_pass": fp,
        "p50_ms": int(statistics.median(lat)), "p95_ms": int(sorted(lat)[int(len(lat) * 0.95) - 1]),
        "cost_rmb": round(cost, 4), "calls": len(per_case),
        "wall_seconds": round(wall_s, 1), "per_case": per_case,
        "usage": summary,
    }


def render_markdown(results: list[dict], rag_results: list[dict] | None = None) -> str:
    lines = [
        "# W1 LLM 选型评测报告（D1 决议）",
        "",
        f"- 生成时间：{dt.datetime.now().isoformat(timespec='seconds')}",
        "- 用例：eval/cases/llm_selection_nl2sql.jsonl（10 NL2SQL：7 单表+3 JOIN）+ rag_single_doc.jsonl（8 RAG）",
        "- 重复：每用例 3 次（响应缓存关闭，真实调用）",
        "- 评分：NL2SQL 执行准确率；RAG answer_hit/retrieval_hit",
        "",
        "| 指标 | " + " | ".join(f"{r['provider']} ({r['model']})" for r in results) + " |",
        "|---|" + "---|" * len(results),
    ]
    for key, label in [("accuracy", "执行准确率"), ("first_pass", "免修复首过率"),
                       ("p50_ms", "P50 延迟 (ms)"), ("p95_ms", "P95 延迟 (ms)"),
                       ("cost_rmb", "成本 (¥)")]:
        lines.append(f"| {label} | " + " | ".join(
            (f"{r[key]:.1%}" if key in ("accuracy", "first_pass") else str(r[key]))
            for r in results) + " |")
    if rag_results:
        lines += ["", "## RAG 维度", "",
                  "| 指标 | " + " | ".join(f"{r['provider']} ({r['model']})" for r in rag_results) + " |",
                  "|---|" + "---|" * len(rag_results)]
        for key, label in [("accuracy", "答案事实命中"), ("retrieval_hit", "检索命中"),
                           ("p50_ms", "P50 延迟 (ms)"), ("cost_rmb", "成本 (¥)")]:
            lines.append(f"| {label} | " + " | ".join(
                (f"{r[key]:.1%}" if key in ("accuracy", "retrieval_hit") else str(r[key]))
                for r in rag_results) + " |")
    best = max(results, key=lambda r: (r["accuracy"], -r["cost_rmb"]))
    lines += ["", f"**建议主力**：`{best['provider']}`（准确率优先；差距 <5% 时按成本/延迟裁决 — PLAN §10）",
              "", "> 注：本报告由 eval/model_selection.py 自动生成，进入技术文档附录。"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", nargs="+", default=["deepseek", "qwen"])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--cases", default=str(Path(__file__).parent / "cases/llm_selection_nl2sql.jsonl"))
    ap.add_argument("--rag-cases", default=str(Path(__file__).parent / "cases/rag_single_doc.jsonl"),
                    help="RAG dimension (pass empty string to skip)")
    args = ap.parse_args()

    cases = [json.loads(l) for l in Path(args.cases).read_text(encoding="utf-8").splitlines() if l.strip()]
    # model selection must measure real calls — disable response cache via env flag
    import os
    os.environ["LLM_RESPONSE_CACHE"] = "0"

    results = []
    for p in args.providers:
        print(f"· evaluating provider={p} (NL2SQL) …")
        res = eval_provider(p, cases, args.repeats)
        print(f"  acc={res['accuracy']:.0%} first_pass={res['first_pass']:.0%} "
              f"p50={res['p50_ms']}ms cost=¥{res['cost_rmb']}")
        results.append(res)

    rag_results = []
    if args.rag_cases and Path(args.rag_cases).exists():
        rag_cases = [json.loads(l) for l in Path(args.rag_cases).read_text(encoding="utf-8").splitlines() if l.strip()]
        for p in args.providers:
            print(f"· evaluating provider={p} (RAG) …")
            res = eval_rag_provider(p, rag_cases, args.repeats)
            print(f"  answer_hit={res['accuracy']:.0%} retrieval_hit={res['retrieval_hit']:.0%} "
                  f"p50={res['p50_ms']}ms cost=¥{res['cost_rmb']}")
            rag_results.append(res)

    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    out = var_dir() / "eval" / f"model_selection_{stamp}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(results, rag_results), encoding="utf-8")
    json_out = out.with_suffix(".json")
    json_out.write_text(json.dumps({"nl2sql": results, "rag": rag_results},
                                   ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    print(f"\nreport: {out}")
    print(f"detail: {json_out}")


if __name__ == "__main__":
    main()
