"""Cross-source multi-hop eval runner (PLAN §4.5/§6.2, W4-D2).

Cases cover the five cross-source types; each asserts routing correctness
(intent / tool set / sub-task count) and answer quality (expected-fact hit).
Routing metrics are cheap and deterministic; fact hit is a quality proxy
until LLM-as-judge lands.

Usage: python eval/run_cross_source.py [--cases .../cross_source.jsonl]
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

from app.agent.kernel import AgentKernel  # noqa: E402
from app.core.config import get_settings, var_dir  # noqa: E402


def _tools_used(trace: dict) -> set[str]:
    """Collect tool names from subtask labels + top-level tool nodes."""
    found: set[str] = set()
    top = {"nl2sql", "rag_search", "formula_eval"}
    for c in trace.get("root", {}).get("children", []):
        label = c.get("label", "")
        if label.startswith("subtask_"):
            found.add(label.split(":", 1)[1])
        elif label in top:
            found.add(label)
    return found


def _subtask_count(trace: dict) -> int:
    for c in trace.get("root", {}).get("children", []):
        if c.get("label") == "plan":
            return len(c.get("detail", {}).get("sub_tasks", []))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(Path(__file__).parent / "cases/cross_source.jsonl"))
    args = ap.parse_args()

    s = get_settings()
    cases = [json.loads(l) for l in Path(args.cases).read_text(encoding="utf-8").splitlines()
             if l.strip()]
    print(f"provider={s.llm_provider} model={s.active_model} cases={len(cases)}\n")

    kernel = AgentKernel()
    results = []
    for case in cases:
        t0 = time.monotonic()
        r = kernel.run(case["question"], session_id=f"cs-{case['id']}-{int(t0)}")
        tools = _tools_used(r.trace)
        n_sub = _subtask_count(r.trace)
        want_clarify = bool(case.get("clarify"))

        if want_clarify:
            intent_ok = r.status == "clarify"
        else:
            intent_ok = r.intent == case["expected_intent"]
        tools_ok = set(case["expected_tools"]).issubset(tools)
        sub_ok = n_sub >= case["min_subtasks"]
        facts = case.get("expected_facts") or []
        fact_hit = (any(f in r.answer for f in facts) if facts else None)

        results.append({
            "id": case["id"], "type": case["type"],
            "intent": r.intent, "status": r.status,
            "tools_used": sorted(tools), "n_subtasks": n_sub,
            "intent_ok": intent_ok, "tools_ok": tools_ok, "subtasks_ok": sub_ok,
            "facts_expected": facts,
            "facts_hit": fact_hit, "n_citations": len(r.citations),
            "latency_ms": r.latency_ms,
        })
        marks = "✓" if (intent_ok and tools_ok and sub_ok and fact_hit is not False) else "✗"
        print(f"  {marks} {case['id']} [{case['type']:8}] intent={r.intent}({'ok' if intent_ok else 'BAD'}) "
              f"tools={sorted(tools)} sub={n_sub} facts={'n/a' if fact_hit is None else ('hit' if fact_hit else 'MISS')} "
              f"cite={len(r.citations)} {r.latency_ms}ms")
        if not intent_ok:
            print(f"      expected intent {case['expected_intent']} / clarify={want_clarify}")
        if fact_hit is False:
            print(f"      expected facts: {facts}")

    n = len(results)
    intent_acc = sum(r["intent_ok"] for r in results) / n
    tools_acc = sum(r["tools_ok"] for r in results) / n
    sub_acc = sum(r["subtasks_ok"] for r in results) / n
    scored = [r for r in results if r["facts_hit"] is not None]
    fact_acc = (sum(r["facts_hit"] for r in scored) / len(scored)) if scored else 1.0
    print(f"\nintent routing: {intent_acc:.0%}   tool routing: {tools_acc:.0%}"
          f"   subtask plan: {sub_acc:.0%}   fact hit: {fact_acc:.0%} ({len(scored)} scored)")

    out = var_dir() / "eval" / f"cross_source_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "provider": s.llm_provider, "model": s.active_model,
        "intent_routing": intent_acc, "tool_routing": tools_acc,
        "subtask_plan": sub_acc, "fact_hit": fact_acc, "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {out.relative_to(BACKEND.parent)}")


if __name__ == "__main__":
    main()
