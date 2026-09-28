"""Multi-turn script runner (PLAN §4.6 验收：脚本化连续对话，含改写/追问/
话题切换/澄清恢复四种模式)。

Case format (eval contract extension): one JSONL line = one session script:
  {"id": "mts-001", "category": "multiturn_script",
   "turns": [{"q": "...", "expect": {
       "status": "ok|clarify|degraded",
       "intent": "DB_QUERY|DOC_QUERY|CHAT|HYBRID|AMBIGUOUS",
       "facts": ["必须全部出现在答案中"],        # 空白不敏感
       "rewritten_contains": ["改写后问题须包含"]  # 指代消解验收
   }}]}

Isolation: each script gets a fresh session_id. Scoring: per-turn + per-script
(all turns). C owns extending this to the 8-script eval set (W3).

Usage: python eval/run_multiturn.py [--cases .../multiturn_scripts.jsonl]
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


def _norm(s: str) -> str:
    return "".join(str(s).split())


def check_turn(result, expect: dict) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if "status" in expect and result.status != expect["status"]:
        errors.append(f"status={result.status} 期望 {expect['status']}")
    if "intent" in expect and result.intent != expect["intent"]:
        errors.append(f"intent={result.intent} 期望 {expect['intent']}")
    if "facts" in expect:
        answer = _norm(result.answer)
        for f in expect["facts"]:
            if _norm(f) not in answer:
                errors.append(f"答案缺少事实 {f!r}")
    if "rewritten_contains" in expect and result.rewritten:
        rw = _norm(result.rewritten)
        for f in expect["rewritten_contains"]:
            if _norm(f) not in rw:
                errors.append(f"改写后问题缺少 {f!r}（实际：{result.rewritten[:50]}）")
    return (not errors), errors


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(Path(__file__).parent / "cases/multiturn_scripts.jsonl"))
    args = ap.parse_args()

    s = get_settings()
    scripts = [json.loads(l) for l in Path(args.cases).read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"provider={s.llm_provider} model={s.active_model} scripts={len(scripts)}\n")

    kernel = AgentKernel()
    results = []
    for script in scripts:
        session_id = f"mts-{script['id']}-{int(time.time())}"
        print(f"== {script['id']}  {script.get('description', '')}")
        turns_ok = 0
        for i, turn in enumerate(script["turns"], 1):
            r = kernel.run(turn["q"], session_id=session_id)
            ok, errors = check_turn(r, turn.get("expect", {}))
            turns_ok += ok
            mark = "✓" if ok else "✗"
            extra = f" → {r.rewritten[:40]}" if r.rewritten else ""
            print(f"  T{i} {mark} [{r.status}/{r.intent}] {turn['q'][:24]}{extra}")
            for e in errors:
                print(f"       {e}")
        results.append({"id": script["id"], "turns": len(script["turns"]),
                        "passed_turns": turns_ok,
                        "passed": turns_ok == len(script["turns"])})
        print()

    n = len(results)
    scripts_passed = sum(r["passed"] for r in results)
    turns_total = sum(r["turns"] for r in results)
    turns_passed = sum(r["passed_turns"] for r in results)
    print(f"scripts: {scripts_passed}/{n}   turns: {turns_passed}/{turns_total}")

    out = var_dir() / "eval" / f"multiturn_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "provider": s.llm_provider, "model": s.active_model,
        "scripts_passed": scripts_passed, "scripts_total": n,
        "turns_passed": turns_passed, "turns_total": turns_total,
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {out.relative_to(BACKEND.parent)}")


if __name__ == "__main__":
    main()
