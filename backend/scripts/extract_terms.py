"""Term dictionary extractor v1 (W1, 中级任务 #1/#2 的数据地基).

Strategy (PLAN §4.2): knowledge lives in the DB, not in prompts.
- Auto-extract low-cardinality enum-like columns (Genre, MediaType, Country…)
  into biz_term (source='auto'), aliases=[canonical] initially.
- Seed manual aliases (source='seed', e.g. "帝都"→北京) from a JSON file.
- LLM batch alias generation + human review lands in W3 (source='llm').

Re-running is incremental: existing terms are kept (preserve human edits).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402

# (table, column, term_type, description) — low-cardinality entity dims of Chinook.
EXTRACT_COLUMNS: list[tuple[str, str, str, str]] = [
    ("genre", "name", "entity", "音乐曲风（genre 表标准名）"),
    ("mediatype", "name", "entity", "媒体格式（mediatype 表标准名）"),
    ("playlist", "name", "entity", "歌单名称（playlist 表标准名）"),
    ("customer", "country", "entity", "客户所在国家"),
    ("customer", "city", "entity", "客户所在城市"),
    ("employee", "firstname", "entity", "员工名（employee 表）"),
]

MAX_CARDINALITY = 60          # skip high-cardinality columns
MIN_VALUE_LEN, MAX_VALUE_LEN = 2, 40


def extract_terms() -> int:
    s = get_settings()
    inserted = 0
    with psycopg.connect(s.database_url, autocommit=True) as pg, pg.cursor() as cur:
        for table, column, term_type, desc in EXTRACT_COLUMNS:
            cur.execute(
                f'SELECT DISTINCT "{column}" FROM "{table}" '
                f'WHERE "{column}" IS NOT NULL ORDER BY 1'
            )
            values = [r[0] for r in cur.fetchall()]
            if len(values) > MAX_CARDINALITY:
                print(f"· skip {table}.{column}: cardinality {len(values)} > {MAX_CARDINALITY}")
                continue
            for v in values:
                v = str(v).strip()
                if not (MIN_VALUE_LEN <= len(v) <= MAX_VALUE_LEN):
                    continue
                cur.execute(
                    """
                    INSERT INTO biz_term (canonical_name, aliases, term_type, binding,
                                          description, source)
                    VALUES (%s, %s, %s, %s, %s, 'auto')
                    ON CONFLICT (canonical_name, binding) DO NOTHING
                    """,
                    (v, [v], term_type, f"{table}.{column}", desc),
                )
                inserted += cur.rowcount
            print(f"  {table}.{column:<12} -> {len(values)} distinct values")
    return inserted


def seed_aliases(seed_file: Path) -> int:
    """Seed file format: [{"canonical":"北京","binding":"Customer.Country",
    "aliases":["帝都","北平"],"description":"..."}]"""
    if not seed_file.exists():
        return 0
    seeds = json.loads(seed_file.read_text(encoding="utf-8"))
    s = get_settings()
    n = 0
    with psycopg.connect(s.database_url, autocommit=True) as pg, pg.cursor() as cur:
        for item in seeds:
            cur.execute(
                """
                INSERT INTO biz_term (canonical_name, aliases, term_type, binding,
                                      description, source)
                VALUES (%s, %s, %s, %s, %s, 'seed')
                ON CONFLICT (canonical_name, binding) DO UPDATE
                SET aliases = ARRAY(SELECT DISTINCT unnest(biz_term.aliases || EXCLUDED.aliases))
                """,
                (item["canonical"], item["aliases"], item.get("term_type", "entity"),
                 item["binding"], item.get("description", "")),
            )
            n += cur.rowcount
    return n


def _default_seed() -> str:
    for base in (BACKEND, BACKEND.parent):
        p = base / "data/db_schema/term_seeds.json"
        if p.exists():
            return str(p)
    return str(BACKEND.parent / "data/db_schema/term_seeds.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default=_default_seed())
    args = ap.parse_args()

    n_auto = extract_terms()
    n_seed = seed_aliases(Path(args.seed))
    print(f"✓ terms auto-extracted: {n_auto}, seed aliases merged: {n_seed}")

    s = get_settings()
    with psycopg.connect(s.database_url, autocommit=True) as pg, pg.cursor() as cur:
        cur.execute("SELECT source, count(*) FROM biz_term GROUP BY source")
        for source, cnt in cur.fetchall():
            print(f"    biz_term[{source}] = {cnt}")


if __name__ == "__main__":
    main()
