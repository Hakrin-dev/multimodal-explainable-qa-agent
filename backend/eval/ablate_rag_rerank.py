"""Compare RRF retrieval against cross-encoder reranking."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings, var_dir  # noqa: E402
from app.rag.pipeline import RAGPipeline  # noqa: E402


def _norm(text: str) -> str:
    return "".join(text.split())


def _evidence_rank(hits, facts: list[str]) -> int | None:
    """First rank whose cumulative text covers every expected fact."""
    accumulated = ""
    normalized_facts = [_norm(fact) for fact in facts]

    for rank, hit in enumerate(hits, start=1):
        accumulated += hit.chunk.text
        normalized = _norm(accumulated)
        if all(fact in normalized for fact in normalized_facts):
            return rank

    return None


def _metrics(rows: list[dict], prefix: str) -> dict:
    ranks = [row[f"{prefix}_rank"] for row in rows]
    latencies = [row[f"{prefix}_latency_ms"] for row in rows]

    return {
        "hit_at_1": sum(rank == 1 for rank in ranks) / len(ranks),
        "hit_at_6": sum(
            rank is not None and rank <= 6
            for rank in ranks
        ) / len(ranks),
        "mrr": sum(
            1.0 / rank if rank is not None else 0.0
            for rank in ranks
        ) / len(ranks),
        "latency_p50_ms": float(np.percentile(latencies, 50)),
        "latency_p95_ms": float(np.percentile(latencies, 95)),
        "latency_mean_ms": statistics.fmean(latencies),
    }


def _top_ids(hits, limit: int = 6) -> list[str]:
    return [hit.chunk.doc_id for hit in hits[:limit]]


def _identity(hit) -> tuple:
    return (
        hit.chunk.doc_id,
        hit.chunk.page_start,
        hit.chunk.text,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        default=str(
            Path(__file__).parent
            / "cases"
            / "rag_single_doc.jsonl"
        ),
    )
    parser.add_argument("--candidate-k", type=int, default=20)
    args = parser.parse_args()

    settings = get_settings()
    if not settings.rerank_enabled:
        raise SystemExit(
            "RERANK_ENABLED must be true for this ablation"
        )

    cases = [
        json.loads(line)
        for line in Path(args.cases)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    pipeline = RAGPipeline()
    pipeline.ensure_loaded()
    retriever = pipeline.retriever
    reranker = retriever.reranker

    if reranker is None:
        raise SystemExit("reranker was not initialized")

    # Warm up both paths so latency metrics exclude model cold start.
    warm_question = cases[0]["question"]

    retriever.reranker = None
    retriever.search(
        warm_question,
        top_k=args.candidate_k,
    )

    retriever.reranker = reranker
    retriever.search(
        warm_question,
        top_k=args.candidate_k,
    )

    rows = []

    for case in cases:
        question = case["question"]
        facts = case["expected_facts"]

        retriever.reranker = None
        started = time.perf_counter()
        rrf_hits = retriever.search(
            question,
            top_k=args.candidate_k,
        )
        rrf_latency = (time.perf_counter() - started) * 1000

        retriever.reranker = reranker
        started = time.perf_counter()
        rerank_hits = retriever.search(
            question,
            top_k=args.candidate_k,
        )
        rerank_latency = (time.perf_counter() - started) * 1000

        rrf_rank = _evidence_rank(rrf_hits, facts)
        rerank_rank = _evidence_rank(rerank_hits, facts)

        top1_changed = (
            bool(rrf_hits)
            and bool(rerank_hits)
            and _identity(rrf_hits[0]) != _identity(rerank_hits[0])
        )

        row = {
            "id": case["id"],
            "question": question,
            "rrf_rank": rrf_rank,
            "rerank_rank": rerank_rank,
            "rrf_latency_ms": round(rrf_latency, 3),
            "rerank_latency_ms": round(rerank_latency, 3),
            "rrf_top6": _top_ids(rrf_hits),
            "rerank_top6": _top_ids(rerank_hits),
            "top1_changed": top1_changed,
        }
        rows.append(row)

        print(
            f"{case['id']} "
            f"rank {rrf_rank}->{rerank_rank} "
            f"top1_changed={top1_changed} "
            f"latency {rrf_latency:.1f}->{rerank_latency:.1f}ms"
        )

    retriever.reranker = reranker

    report = {
        "config": {
            "database": settings.postgres_db,
            "embedding_model": settings.embedding_model_path,
            "rerank_model": settings.rerank_model_path,
            "candidate_k": args.candidate_k,
            "cases": len(cases),
        },
        "rrf": _metrics(rows, "rrf"),
        "rerank": _metrics(rows, "rerank"),
        "results": rows,
    }

    print("\nRRF:")
    print(json.dumps(report["rrf"], indent=2))

    print("RERANK:")
    print(json.dumps(report["rerank"], indent=2))

    output = (
        var_dir()
        / "eval"
        / (
            "rag_rerank_ablation_"
            + dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            + ".json"
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("report:", output.relative_to(BACKEND.parent))


if __name__ == "__main__":
    main()
