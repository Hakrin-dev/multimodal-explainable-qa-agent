"""RAG eval runner v0 (B/C: LLM-as-judge 双评在 W2 补).

Scoring (two separate metrics — they localize failure):
- retrieval_hit: expected facts present in the TOP-K retrieved chunks
  (embedding/retrieval quality; no LLM involved)
- answer_hit:    expected facts present in the generated answer
  (end-to-end; RAG 忠实度 proxy until judge is in)

Usage: python eval/run_rag.py [--cases .../rag_single_doc.jsonl] [--top-k 6]
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
from app.rag.pipeline import RAGPipeline  # noqa: E402


def _norm(s: str) -> str:
    return "".join(s.split())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(Path(__file__).parent / "cases/rag_single_doc.jsonl"))
    ap.add_argument("--top-k", type=int, default=6)
    args = ap.parse_args()

    s = get_settings()
    cases = [json.loads(l) for l in Path(args.cases).read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"provider={s.llm_provider} model={s.active_model} "
          f"embedding={s.embedding_provider} cases={len(cases)}\n")

    pipeline = RAGPipeline()
    pipeline.ensure_loaded()

    results = []
    for case in cases:
        t0 = time.monotonic()
        r = pipeline.run(case["question"], top_k=args.top_k)
        retrieved_text = _norm("".join(h["text"] for h in r.hits))
        answer_norm = _norm(r.answer)
        facts = case["expected_facts"]
        ret_hit = all(_norm(f) in retrieved_text for f in facts)
        ans_hit = all(_norm(f) in answer_norm for f in facts)
        results.append({
            "id": case["id"], "retrieval_hit": ret_hit, "answer_hit": ans_hit,
            "status": r.status, "latency_ms": r.latency_ms,
            "n_citations": len(r.citations),
        })
        marks = ("✓" if ans_hit else ("◐" if ret_hit else "✗"))
        print(f"  {marks} {case['id']}  ret={'✓' if ret_hit else '✗'} ans={'✓' if ans_hit else '✗'}"
              f"  {r.latency_ms:>4}ms  citations={len(r.citations)}")
        if not ret_hit:
            print(f"      retrieval missed facts: {facts}")

    n = len(results)
    ret_acc = sum(r["retrieval_hit"] for r in results) / n
    ans_acc = sum(r["answer_hit"] for r in results) / n
    print(f"\nretrieval hit@{args.top_k}: {ret_acc:.0%}   answer hit: {ans_acc:.0%}"
          f"   (with mock LLM, answer hit is expected 0% — it proves plumbing only)")

    out = var_dir() / "eval" / f"rag_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "provider": s.llm_provider, "model": s.active_model,
        "embedding_provider": s.embedding_provider,
        "retrieval_hit": ret_acc, "answer_hit": ans_acc, "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {out.relative_to(BACKEND.parent)}")


if __name__ == "__main__":
    main()
