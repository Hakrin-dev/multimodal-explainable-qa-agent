"""W1 RAG smoke: real embedding + real retrieval over the 3 KB docs,
scripted-mock generation (no API key needed).

Proves: PDF parse → heading detection → breadcrumb chunking → pgvector +
BM25 hybrid retrieval (REAL, quantified) → cited generation (mock) → trace.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.llm import MockProvider, get_llm_service  # noqa: E402

# retrieval checks: question -> fact that must appear in top-6 retrieved text
RETRIEVAL_CHECKS = [
    ("正式员工每年事假上限几天？", "5 天"),
    ("入职满五年年假多少天？", "15 天"),
    ("VIP 客户工单响应时限？", "4 小时"),
    ("销售岗月度提成怎么算？", "3%"),
    ("音频质量问题几天内可以更换？", "7 天"),
    ("Jane Peacock 的工作方法论是什么？", "复购"),
    ("销售支持专员的月度销售目标是多少？", "10 万元"),
    ("报销审批链是什么？", "直属上级"),
]


def _norm(s: str) -> str:
    """Whitespace-insensitive matching (PDF hard-wraps split tokens)."""
    return "".join(s.split())


def main() -> None:
    from app.rag.pipeline import RAGPipeline

    pipeline = RAGPipeline()
    n = pipeline.ensure_loaded()
    print(f"chunks loaded: {n}")
    assert n > 0, "run scripts/ingest_docs.py first"

    print("\n== retrieval recall (real embedding+BM25, no LLM)")
    ok = 0
    for q, fact in RETRIEVAL_CHECKS:
        hits = pipeline.retriever.search(q, top_k=6)
        hit_texts = "".join(h.chunk.text for h in hits)
        found = _norm(fact) in _norm(hit_texts)
        ok += found
        top = hits[0]
        print(f"  {'✓' if found else '✗'} {q}  -> top1 p{top.chunk.page_start}"
              f" [{top.chunk.doc_name}] score={top.score:.3f}")
    print(f"retrieval recall@6: {ok}/{len(RETRIEVAL_CHECKS)}")

    # pipeline mechanics with scripted mock LLM
    get_llm_service().register_mock(MockProvider(scripted=[
        "根据员工手册，事假每年上限为 5 天[1]。",
        "销售岗月度提成 = 当月个人销售额 × 3% + 超额部分 × 2%[1]。",
    ]))
    print("\n== pipeline (mock generation)")
    for q in ["事假每年最多几天？", "销售提成公式是什么？"]:
        r = pipeline.run(q)
        labels = [c["label"] for c in r.trace["root"]["children"]]
        print(f"  {q} -> {r.answer[:40]}… | nodes={labels} | "
              f"citations={len(r.citations)} | {r.latency_ms}ms")


if __name__ == "__main__":
    main()
