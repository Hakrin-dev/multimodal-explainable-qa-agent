"""Ingest KB docs from data/docs_raw into PG (vector + metadata).

Usage:
    python scripts/ingest_docs.py [--dir ../data/docs_raw] [--force]

Idempotent: unchanged docs (same content hash) are skipped.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import resolve_repo_path  # noqa: E402


def _default_docs_dir() -> Path:
    """Container: /app/data/docs_raw (volume mount); host: repo/data/docs_raw."""
    return resolve_repo_path("data/docs_raw")

from app.rag.embedding import get_embedding_service  # noqa: E402
from app.rag.pipeline import ingest_document  # noqa: E402
from app.rag.store import KBStore  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(_default_docs_dir()))
    ap.add_argument("--force", action="store_true", help="re-ingest even if unchanged")
    args = ap.parse_args()

    emb = get_embedding_service()
    store = KBStore(dim=emb.dim)
    pdfs = sorted(Path(args.dir).glob("*.pdf"))
    if not pdfs:
        print(f"no PDFs in {args.dir} — run scripts/gen_kb_docs.py first")
        sys.exit(1)

    total_new = 0
    for p in pdfs:
        doc, n_new = ingest_document(str(p), store=store, embedding=emb,
                                     force=args.force)
        state = "re-ingested" if n_new else "unchanged (skipped)"
        print(f"  {p.name:<28} pages={doc.pages:>2} blocks={len(doc.blocks):>3} "
              f"chunks={len(doc.chunks):>3}  {state}")
        total_new += n_new
    print(f"✓ ingest done: {len(pdfs)} docs, {total_new} new chunks "
          f"(provider={emb.settings.embedding_provider}, dim={emb.dim})")


if __name__ == "__main__":
    main()
