"""C role's reproducible L0/L1/L2 evaluation entrypoint.

L0 selects five cases per evaluation family (NL2SQL, RAG, multi-turn), L1
runs every checked-in case once, and L2 repeats the full set for each provider
and repetition. Existing family runners remain the scoring authority; this
script only plans and dispatches them, writing a machine-readable manifest.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CASES = BACKEND / "eval" / "cases"
DEFAULTS = {
    "nl2sql": [CASES / "nl2sql_single_table.jsonl", CASES / "nl2sql_multi_table.jsonl", CASES / "nl2sql_robustness.jsonl"],
    "rag": [CASES / "rag_single_doc.jsonl"],
    "multiturn": [CASES / "multiturn_scripts.jsonl"],
    "cross_source": [CASES / "cross_source.jsonl"],
    "clarify": [CASES / "clarify_questions.jsonl"],
}


def _read_cases(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return rows


def _ids(family: str, level: str) -> str:
    if family == "nl2sql" and level == "L0":
        # Keep the smoke gate representative: single-table, JOIN, and robust variants.
        rows = _read_cases([DEFAULTS[family][0]])[:3] + _read_cases([DEFAULTS[family][1]])[:2]
        return ",".join(row["id"] for row in rows)
    rows = _read_cases(DEFAULTS[family])
    return ",".join(row["id"] for row in (rows[:5] if level == "L0" else rows))


def _command(family: str, level: str) -> list[str]:
    script = BACKEND / "eval" / {"nl2sql": "run_nl2sql.py", "rag": "run_rag.py", "multiturn": "run_multiturn.py", "cross_source": "run_cross_source.py", "clarify": "run_clarify.py"}[family]
    cmd = [sys.executable, str(script)]
    if family == "nl2sql":
        cmd += ["--cases", *map(str, DEFAULTS[family])]
    else:
        cmd += ["--cases", str(DEFAULTS[family][0])]
    if level == "L0":
        cmd += ["--only-ids", _ids(family, level)]
    return cmd


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the C-role evaluation levels")
    ap.add_argument("--level", choices=("L0", "L1", "L2"), required=True)
    ap.add_argument("--provider", default=None, help="provider for L0/L1 (defaults to .env)")
    ap.add_argument("--providers", default=None, help="comma-separated providers for L2")
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()
    if args.repeats < 1:
        ap.error("--repeats must be >= 1")
    providers = ([p.strip() for p in (args.providers or args.provider or "mock").split(",") if p.strip()]
                 if args.level == "L2" else [args.provider])
    repetitions = args.repeats if args.level == "L2" else 1
    manifest = {"level": args.level, "started_at": dt.datetime.now().isoformat(timespec="seconds"),
                "providers": providers, "repeats": repetitions, "runs": []}
    for provider in providers:
        for repeat in range(1, repetitions + 1):
            for family in DEFAULTS:
                cmd = _command(family, args.level)
                env = os.environ.copy()
                if provider:
                    env["LLM_PROVIDER"] = provider
                    # clear any .env LLM_MODEL override so active_model falls
                    # back to the provider default (e.g. qwen-plus), otherwise a
                    # stale model name leaks across providers (404 model_not_found)
                    env["LLM_MODEL"] = ""
                print(f"\n=== {args.level} provider={provider or 'configured'} repeat={repeat} family={family} ===")
                completed = subprocess.run(cmd, cwd=BACKEND, env=env, check=False)
                attempts = 1
                # infra retry: WSL2 docker-proxy can drop port 5433 under
                # sustained mixed load; retry the family rather than abort L1.
                while completed.returncode != 0 and attempts < 4:
                    attempts += 1
                    print(f"  [infra] family={family} rc={completed.returncode} — retry {attempts - 1}/3 after 15s")
                    time.sleep(15)
                    completed = subprocess.run(cmd, cwd=BACKEND, env=env, check=False)
                manifest["runs"].append({"family": family, "provider": provider or "configured",
                                         "repeat": repeat, "command": cmd, "returncode": completed.returncode, "attempts": attempts})
    manifest["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
    out = BACKEND / "var" / "eval" / f"levels_{args.level.lower()}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = sum(run["returncode"] != 0 for run in manifest["runs"])
    print(f"\nmanifest: {out.relative_to(BACKEND.parent)}")
    print(f"runs: {len(manifest['runs'])}, failures: {failures}")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
