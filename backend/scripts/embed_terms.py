"""Term embedding backfill (W3, 中级任务 #1/#2 向量化地基).

- Fixes biz_term.embedding column dim to the active embedding service dim
  (DDL shipped VECTOR(1024); local bge-small-zh-v1.5 is 512 — column was
  unused so far, so ALTER is safe).
- Embeds canonical + aliases + description + binding per term.
- Adds an HNSW cosine index (tiny table, but future-proof).

Idempotent: skips terms that already have an embedding (pass --all to recompute).

Usage:
    python scripts/embed_terms.py [--all]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.db.session import get_conn  # noqa: E402
from app.rag.embedding import get_embedding_service  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="recompute all embeddings")
    args = ap.parse_args()

    s = get_settings()
    svc = get_embedding_service()
    dim = svc.dim
    print(f"embedding provider={s.embedding_provider} dim={dim}")

    with get_conn(readonly=False) as conn, conn.cursor() as cur:
        # 1) align column dim with the active embedding model
        cur.execute(
            "SELECT atttypmod FROM pg_attribute a JOIN pg_class c ON a.attrelid = c.oid "
            "WHERE c.relname = 'biz_term' AND a.attname = 'embedding'")
        row = cur.fetchone()
        col_dim = row[0] if row else None  # pgvector stores dim in atttypmod
        if col_dim and col_dim != dim:
            print(f"· ALTER biz_term.embedding vector({col_dim}) -> vector({dim})")
            cur.execute(f"ALTER TABLE biz_term ALTER COLUMN embedding TYPE vector({dim}) "
                        "USING NULL::vector")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS biz_term_emb_hnsw "
            "ON biz_term USING hnsw (embedding vector_cosine_ops)")

        # 2) backfill
        cur.execute(
            "SELECT id, canonical_name, aliases, description, binding "
            + ("FROM biz_term" if args.all else
               "FROM biz_term WHERE embedding IS NULL") + " ORDER BY id")
        rows = cur.fetchall()
        if not rows:
            print("· all terms already embedded")
            return
        texts = [
            f"{canonical}；别名：{', '.join(list(aliases or []))}；"
            f"{description or ''}；字段：{binding}".strip()
            for _, canonical, aliases, description, binding in rows
        ]
        embs = svc.embed(texts)
        for (tid, *_), emb in zip(rows, embs):
            cur.execute("UPDATE biz_term SET embedding = %s WHERE id = %s",
                        (emb.tolist(), tid))
        print(f"· embedded {len(rows)} terms")


if __name__ == "__main__":
    main()
