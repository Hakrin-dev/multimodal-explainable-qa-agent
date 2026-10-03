"""Cross-source multi-hop eval runner (PLAN §4.5/§6.2, W4-D2/D3).

Cases cover the five cross-source types. Single-turn cases assert routing
(intent / tool set / sub-task count) + answer quality (expected-fact hit);
multi-turn cases (cs-011 type⑤) assert per-turn status / tools / facts /
coreference rewriting under one session.

Usage: python eval/run_cross_source.py [--cases .../cross_source.jsonl] [--only-ids cs-003,cs-011]
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


def _run_single(kernel, case, sid) -> dict:
    r = kernel.run(case["question"], session_id=sid)
    tools = _tools_used(r.trace)
    n_sub = _subtask_count(r.trace)
    want_clarify = bool(case.get("clarify"))
    intent_ok = (r.status == "clarify") if want_clarify else (r.intent == case["expected_intent"])
    tools_ok = set(case["expected_tools"]).issubset(tools)
    sub_ok = n_sub >= case["min_subtasks"]
    facts = case.get("expected_facts") or []
    fact_hit = (any(f in r.answer for f in facts) if facts else None)
    ok = intent_ok and tools_ok and sub_ok and fact_hit is not False
    return {"id": case["id"], "type": case["type"], "mode": "single", "ok": ok,
            "intent": r.intent, "status": r.status, "tools_used": sorted(tools),
            "n_subtasks": n_sub, "intent_ok": intent_ok, "tools_ok": tools_ok,
            "subtasks_ok": sub_ok, "facts_expected": facts, "facts_hit": fact_hit,
            "n_citations": len(r.citations), "latency_ms": r.latency_ms}


def _run_turns(kernel, case, sid) -> dict:
    turns = []
    all_ok = True
    for t in case["turns"]:
        r = kernel.run(t["q"], session_id=sid)
        exp = t.get("expect", {})
        tools = _tools_used(r.trace)
        status_ok = r.status == exp["status"] if "status" in exp else True
        intent_ok = r.intent == exp["intent"] if "intent" in exp else True
        tools_ok = set(exp.get("tools", [])).issubset(tools)
        facts = exp.get("facts", [])
        fact_hit = (any(f in r.answer for f in facts) if facts else None)
        rw = exp.get("rewritten_contains", [])
        rw_ok = all(x in (r.rewritten or "") for x in rw) if rw else True
        ok = status_ok and intent_ok and tools_ok and (fact_hit is not False) and rw_ok
        all_ok = all_ok and ok
        turns.append({"q": t["q"], "ok": ok, "status": r.status, "intent": r.intent,
                      "tools_used": sorted(tools), "status_ok": status_ok,
                      "intent_ok": intent_ok, "tools_ok": tools_ok,
                      "facts_expected": facts, "facts_hit": fact_hit,
                      "rewritten_ok": rw_ok, "rewritten": r.rewritten,
                      "latency_ms": r.latency_ms})
    return {"id": case["id"], "type": case["type"], "mode": "turns",
            "ok": all_ok, "turns": turns}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(Path(__file__).parent / "cases/cross_source.jsonl"))
    ap.add_argument("--only-ids", default="", help="comma-separated case id filter (for level runner)")
    args = ap.parse_args()

    s = get_settings()
    only = {x.strip() for x in args.only_ids.split(",") if x.strip()}
    cases = [json.loads(l) for l in Path(args.cases).read_text(encoding="utf-8").splitlines()
             if l.strip() and (not only or json.loads(l)["id"] in only)]
    print(f"provider={s.llm_provider} model={s.active_model} cases={len(cases)}\n")

    kernel = AgentKernel()
    results = []
    for case in cases:
        sid = f"cs-{case['id']}-{int(time.monotonic() * 1000)}"
        if "turns" in case:
            res = _run_turns(kernel, case, sid)
            print(f"  {'✓' if res['ok'] else '✗'} {case['id']} [{case['type']:14}] "
                  f"multi-turn ({len(res['turns'])} rounds)")
            for i, t in enumerate(res["turns"], 1):
                print(f"      T{i} {'✓' if t['ok'] else '✗'} status={t['status']} "
                      f"tools={t['tools_used']} facts={t['facts_hit']} rw={t['rewritten']!r}")
        else:
            res = _run_single(kernel, case, sid)
            marks = "✓" if res["ok"] else "✗"
            print(f"  {marks} {res['id']} [{res['type']:14}] intent={res['intent']}"
                  f"({'ok' if res['intent_ok'] else 'BAD'}) tools={res['tools_used']} "
                  f"sub={res['n_subtasks']} facts={'n/a' if res['facts_hit'] is None else ('hit' if res['facts_hit'] else 'MISS')} "
                  f"cite={res['n_citations']} {res['latency_ms']}ms")
            if not res["intent_ok"]:
                print(f"      expected intent {case['expected_intent']} / clarify={case.get('clarify')}")
            if res["facts_hit"] is False:
                print(f"      expected facts: {res['facts_expected']}")
        results.append(res)

    n = len(results)
    passed = sum(r["ok"] for r in results)
    singles = [r for r in results if r["mode"] == "single"]
    scored = [r for r in singles if r["facts_hit"] is not None]
    intent_acc = (sum(r["intent_ok"] for r in singles) / len(singles)) if singles else 1.0
    tools_acc = (sum(r["tools_ok"] for r in singles) / len(singles)) if singles else 1.0
    sub_acc = (sum(r["subtasks_ok"] for r in singles) / len(singles)) if singles else 1.0
    fact_acc = (sum(r["facts_hit"] for r in scored) / len(scored)) if scored else 1.0
    turn_total = sum(len(r["turns"]) for r in results if r["mode"] == "turns")
    turn_ok = sum(t["ok"] for r in results if r["mode"] == "turns" for t in r["turns"])
    print(f"\ncases: {passed}/{n} = {passed / n:.0%}"
          f"   intent routing: {intent_acc:.0%}   tool routing: {tools_acc:.0%}"
          f"   subtask plan: {sub_acc:.0%}   fact hit: {fact_acc:.0%} ({len(scored)} scored)")
    if turn_total:
        print(f"multi-turn: {turn_ok}/{turn_total} turns")

    out = var_dir() / "eval" / f"cross_source_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "provider": s.llm_provider, "model": s.active_model,
        "cases_pass": passed / n, "intent_routing": intent_acc, "tool_routing": tools_acc,
        "subtask_plan": sub_acc, "fact_hit": fact_acc,
        "multiturn_turns": f"{turn_ok}/{turn_total}" if turn_total else None,
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {out.relative_to(BACKEND.parent)}")
    # Do NOT exit non-zero on an accuracy shortfall: run_levels treats a
    # non-zero family return code as infra failure and retries the whole
    # family. Accuracy is reported, not signaled; infra errors raise.


if __name__ == "__main__":
    main()
