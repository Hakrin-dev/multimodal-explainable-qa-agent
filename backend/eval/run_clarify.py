"""Clarification-trigger evaluation runner (C W3)."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.agent.kernel import AgentKernel  # noqa: E402
from app.core.config import get_settings, var_dir  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(Path(__file__).parent / "cases/clarify_questions.jsonl"))
    ap.add_argument("--only-ids", default="")
    args = ap.parse_args()
    only = {x.strip() for x in args.only_ids.split(",") if x.strip()}
    cases = [json.loads(line) for line in Path(args.cases).read_text(encoding="utf-8").splitlines()
             if line.strip() and (not only or json.loads(line)["id"] in only)]
    kernel = AgentKernel()
    results = []
    for case in cases:
        r = kernel.run(case["question"], session_id=f"clarify-{case['id']}")
        missing = (r.clarify or {}).get("missing_slots", [])
        ok = r.status == case.get("expected_status", "clarify")
        if case.get("expected_intent"):
            ok = ok and r.intent == case["expected_intent"]
        ok = ok and all(slot in missing for slot in case.get("expected_missing_contains", []))
        results.append({"id": case["id"], "passed": ok, "status": r.status,
                        "intent": r.intent, "missing_slots": missing})
        print(f"  {'✓' if ok else '✗'} {case['id']} [{r.status}/{r.intent}] missing={missing}")
    n = len(results)
    passed = sum(x["passed"] for x in results)
    print(f"\nclarify accuracy: {passed}/{n} ({passed / n:.0%})" if n else "no cases matched")
    out = var_dir() / "eval" / f"clarify_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"provider": get_settings().llm_provider,
                               "passed": passed, "total": n, "results": results},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    # Accuracy shortfalls are evaluation results, not runner/infrastructure failures.
    # Keep the process successful so family-level retry logic does not rerun valid cases.
    raise SystemExit(0 if n else 2)


if __name__ == "__main__":
    main()
