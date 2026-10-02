"""RAG evaluation runner.

Metrics:
- retrieval_hit: expected facts occur in top-k retrieved chunks.
- answer_hit: expected facts occur in the final generated answer.
- faithfulness: optional rag.factcheck metadata and trace evidence.

Usage:
    python eval/run_rag.py [--cases PATH] [--top-k 6]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Iterator

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings, var_dir  # noqa: E402
from app.rag.pipeline import RAGPipeline  # noqa: E402


def _norm(text: str) -> str:
    return "".join(text.split())


def _find_trace_nodes(
    node: dict,
    label: str,
) -> Iterator[dict]:
    if node.get("label") == label:
        yield node

    for child in node.get("children", []):
        yield from _find_trace_nodes(child, label)


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
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument(
        "--only-ids",
        default="",
        help="comma-separated case id filter",
    )
    args = parser.parse_args()

    settings = get_settings()
    only = {
        item.strip()
        for item in args.only_ids.split(",")
        if item.strip()
    }

    cases = []
    for line in Path(args.cases).read_text(
        encoding="utf-8"
    ).splitlines():
        if not line.strip():
            continue

        case = json.loads(line)
        if not only or case["id"] in only:
            cases.append(case)

    print(
        f"provider={settings.llm_provider} "
        f"model={settings.active_model} "
        f"embedding={settings.embedding_provider} "
        f"factcheck={settings.rag_factcheck_enabled} "
        f"cases={len(cases)}\n"
    )

    pipeline = RAGPipeline()
    pipeline.ensure_loaded()

    results = []

    for case in cases:
        result = pipeline.run(
            case["question"],
            top_k=args.top_k,
        )

        retrieved_text = _norm(
            "".join(hit["text"] for hit in result.hits)
        )
        final_answer_norm = _norm(result.answer)
        facts = case["expected_facts"]

        retrieval_hit = all(
            _norm(fact) in retrieved_text
            for fact in facts
        )
        answer_hit = all(
            _norm(fact) in final_answer_norm
            for fact in facts
        )

        root = result.trace.get("root", {})
        factcheck_nodes = list(
            _find_trace_nodes(root, "rag_factcheck")
        )
        factcheck_node = (
            factcheck_nodes[0]
            if factcheck_nodes
            else None
        )
        factcheck_llm_calls = 0

        if factcheck_node is not None:
            factcheck_llm_calls = sum(
                child.get("label") == "llm"
                for child in factcheck_node.get(
                    "children",
                    [],
                )
            )

        initial_answer = (
            result.initial_answer
            or result.answer
        )

        row = {
            "id": case["id"],
            "question": case["question"],
            "expected_facts": facts,
            "retrieval_hit": retrieval_hit,
            "answer_hit": answer_hit,
            "status": result.status,
            "latency_ms": result.latency_ms,
            "n_citations": len(result.citations),
            "initial_answer": initial_answer,
            "final_answer": result.answer,
            "answer_changed": (
                initial_answer != result.answer
            ),
            "faithfulness": result.faithfulness,
            "factcheck_trace_present": (
                factcheck_node is not None
            ),
            "factcheck_trace_status": (
                factcheck_node.get("status")
                if factcheck_node
                else None
            ),
            "factcheck_llm_calls": (
                factcheck_llm_calls
            ),
        }
        results.append(row)

        mark = (
            "✓"
            if answer_hit
            else ("◐" if retrieval_hit else "✗")
        )

        factcheck_text = ""
        if settings.rag_factcheck_enabled:
            faithful = result.faithfulness.get(
                "faithful",
                False,
            )
            attempts = result.faithfulness.get(
                "attempts",
                0,
            )
            rewritten = result.faithfulness.get(
                "rewritten",
                False,
            )
            factcheck_text = (
                f"  fc={'✓' if faithful else '✗'}"
                f"/{attempts}"
                f"{'/rewrite' if rewritten else ''}"
            )

        print(
            f"  {mark} {case['id']} "
            f"ret={'✓' if retrieval_hit else '✗'} "
            f"ans={'✓' if answer_hit else '✗'} "
            f"{result.latency_ms:>4}ms "
            f"citations={len(result.citations)}"
            f"{factcheck_text}"
        )

        if not retrieval_hit:
            print(
                "      retrieval missed facts:",
                facts,
            )

    count = len(results)
    if count == 0:
        raise SystemExit("no evaluation cases selected")

    retrieval_accuracy = (
        sum(row["retrieval_hit"] for row in results)
        / count
    )
    answer_accuracy = (
        sum(row["answer_hit"] for row in results)
        / count
    )

    print(
        f"\nretrieval hit@{args.top_k}: "
        f"{retrieval_accuracy:.0%}   "
        f"answer hit: {answer_accuracy:.0%}"
    )

    if settings.llm_provider == "mock":
        print(
            "mock provider note: answer hit may be 0%; "
            "mock primarily verifies plumbing"
        )

    faithful_count = sum(
        row["faithfulness"].get("faithful", False)
        for row in results
    )
    rewritten_count = sum(
        row["faithfulness"].get("rewritten", False)
        for row in results
    )
    degraded_count = sum(
        row["faithfulness"].get("degraded", False)
        for row in results
    )

    if settings.rag_factcheck_enabled:
        print(
            "factcheck: "
            f"faithful={faithful_count}/{count} "
            f"rewritten={rewritten_count}/{count} "
            f"degraded={degraded_count}/{count}"
        )

    output = (
        var_dir()
        / "eval"
        / (
            "rag_"
            + dt.datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )
            + ".json"
        )
    )
    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    report = {
        "provider": settings.llm_provider,
        "model": settings.active_model,
        "embedding_provider": (
            settings.embedding_provider
        ),
        "factcheck_enabled": (
            settings.rag_factcheck_enabled
        ),
        "factcheck_max_rounds": (
            settings.rag_factcheck_max_rounds
        ),
        "response_cache_enabled": (
            settings.llm_response_cache
        ),
        "top_k": args.top_k,
        "case_count": count,
        "retrieval_hit": retrieval_accuracy,
        "answer_hit": answer_accuracy,
        "faithful_count": faithful_count,
        "rewritten_count": rewritten_count,
        "degraded_count": degraded_count,
        "results": results,
    }

    output.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        "report:",
        output.relative_to(BACKEND.parent),
    )


if __name__ == "__main__":
    main()
