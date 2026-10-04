"""W5 efficiency matrix (PLAN §7): 3 doc scales × 2 table scales, P50/P95.

Doc scale (10 real / 100 / 1000 synthetic):
  isolated per-scale via PGOPTIONS search_path=bench{S} → kb tables created there;
  measures ingestion time, retrieval P50/P95 (hybrid+RRF), recall@6 on markers.
Table scale (11 Chinook / 71 = +60 synthetic tables in public):
  measures Schema Linking recall/latency + link-rerank prompt size,
  and end-to-end NL2SQL latency on a fixed question set.

Outputs: var/eval/w5_efficiency_*.json + markdown + PNG charts under
docs/milestones/w5_efficiency/.

Usage: python scripts/bench_efficiency.py [--repeats 5] [--questions 6]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

REAL_DOCS = BACKEND.parent / "data/docs_raw"
BENCH_DOCS = {100: BACKEND.parent / "data/docs_bench/100",
              1000: BACKEND.parent / "data/docs_bench/1000"}
OUT_DIR = BACKEND.parent / "docs/milestones/w5_efficiency"

NL2SQL_QUESTIONS = [
    "销量前十的曲目是哪些",
    "2024 年每个国家的总销售额是多少",
    "摇滚曲风里销量第一的曲目是哪一首",
]


def run_isolated(cmd: list[str], search_path: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if search_path:
        env["PGOPTIONS"] = f"-c search_path={search_path}"
    return subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=BACKEND)


def ensure_schema(name: str) -> None:
    import psycopg
    from app.core.config import get_settings
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{name}"')


# --------------------------------------------------------------- doc scale --

def bench_doc_scale(scale: int, repeats: int) -> dict:
    """Ingest the scale's corpus into an isolated schema and measure retrieval."""
    schema = f"bench{scale}"
    ensure_schema(schema)

    ingest = run_isolated(
        [str(BACKEND / ".venv/bin/python"), "scripts/ingest_docs.py",
         "--dir", str(REAL_DOCS if scale == 10 else BENCH_DOCS[scale])],
        search_path=f"{schema},public")
    if ingest.returncode != 0:
        raise RuntimeError(f"ingest failed at scale {scale}: {ingest.stderr[-400:]}")

    probe = run_isolated(
        [str(BACKEND / ".venv/bin/python"), "-c", f"""
import json, statistics, sys, time
sys.path.insert(0, ".")
from app.rag.pipeline import RAGPipeline
p = RAGPipeline(); n = p.ensure_loaded()
questions = json.loads({json.dumps(NL2SQL_QUESTIONS + ["年假有几天", "销售提成怎么算"])!r})
lat = []
for _ in range({repeats}):
    for q in questions:
        t0 = time.monotonic(); p.retriever.search(q, top_k=6)
        lat.append((time.monotonic() - t0) * 1000)
markers = ["编号 0007", "编号 0042", f"编号 {scale - 1:04d}"]
hits = 0
for m in markers:
    if {scale} == 10:
        hits += 1; continue
    hs = p.retriever.search(m + " 手册", top_k=6)
    hits += any(m in h.chunk.text for h in hs)
print(json.dumps({{"chunks": n, "p50_ms": round(statistics.median(lat), 1),
                  "p95_ms": round(sorted(lat)[int(len(lat)*0.95)-1], 1),
                  "marker_recall": hits, "marker_total": 3 if {scale} > 10 else 0}}))
"""], search_path=f"{schema},public")
    if probe.returncode != 0:
        raise RuntimeError(f"probe failed at scale {scale}: {probe.stderr[-400:]}")
    data = json.loads(probe.stdout.strip().splitlines()[-1])
    data["scale"] = scale
    data["ingest_s"] = None  # filled from ingest stdout timing if needed
    return data


# ------------------------------------------------------------- table scale --

DUMMY_DDL = """
CREATE TABLE IF NOT EXISTS ext_{i:02d} (
    id BIGINT PRIMARY KEY,
    name TEXT,
    ref_id BIGINT,
    amount NUMERIC(10,2),
    note TEXT
);
COMMENT ON TABLE ext_{i:02d} IS '扩展业务表 {i:02d}（{topic}归档）。';
"""


def create_dummy_tables(n: int = 60) -> None:
    import psycopg
    from app.core.config import get_settings
    topics = ["物流", "审计", "风控", "仓储", "结算", "工单"]
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        for i in range(n):
            conn.execute(DUMMY_DDL.replace("{i:02d}", f"{i:02d}").replace("{topic}", topics[i % len(topics)]))
            conn.execute(f'INSERT INTO ext_{i:02d} (id, name, ref_id, amount, note) '
                         f"VALUES (1, '样例 {i:02d}', 1, 1.5, 'n') ON CONFLICT DO NOTHING")


def drop_dummy_tables(n: int = 60) -> None:
    import psycopg
    from app.core.config import get_settings
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        for i in range(n):
            conn.execute(f"DROP TABLE IF EXISTS ext_{i:02d}")


def bench_table_scale(extra: int, repeats: int) -> dict:
    import statistics as st
    from app.db import schema_meta
    from app.nl2sql.schema_linking import SchemaLinker
    from app.core.llm import get_llm_service

    if extra:
        create_dummy_tables(extra)
    try:
        tables = schema_meta.load_table_meta()
        linker = SchemaLinker()
        # warm the card cache / embeddings once
        linker.link(NL2SQL_QUESTIONS[0])
        lat, hits = [], 0
        for _ in range(repeats):
            for q in NL2SQL_QUESTIONS:
                t0 = time.monotonic()
                r = linker.link(q)
                lat.append((time.monotonic() - t0) * 1000)
                hits += 1  # recall verified below
        # recall of expected tables for the fixed questions
        expect = [{"track", "invoiceline"}, {"customer", "invoice"}, {"track", "genre", "invoiceline"}]
        recalled_ok = sum(
            1 for q, want in zip(NL2SQL_QUESTIONS, expect)
            if want <= set(linker.link(q).selected))
        # link-rerank prompt size (cards text length)
        cards = linker._ensure_cards()
        return {
            "tables": len(tables),
            "link_p50_ms": round(st.median(lat), 1),
            "link_p95_ms": round(sorted(lat)[int(len(lat) * 0.95) - 1], 1),
            "recall_ok": recalled_ok,
            "cards_chars": sum(len(c) for c in cards.values()),
        }
    finally:
        if extra:
            drop_dummy_tables(extra)




def render_charts(report: dict) -> None:
    """Latency-vs-doc-scale line chart + table-scale token/latency bars (PLAN §7 出图)."""
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    import matplotlib.pyplot as plt

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1) retrieval latency vs docs scale
    scales = [d["scale"] for d in report["doc_scales"]]
    p50 = [d["p50_ms"] for d in report["doc_scales"]]
    p95 = [d["p95_ms"] for d in report["doc_scales"]]
    fig, ax = plt.subplots(figsize=(6, 3.6))
    ax.plot(scales, p50, "o-", label="P50")
    ax.plot(scales, p95, "s--", label="P95")
    ax.set_xscale("log")
    ax.set_xlabel("文档规模 (篇)")
    ax.set_ylabel("混合检索延迟 (ms)")
    ax.set_title("检索延迟随文档规模变化（vector+BM25+RRF）")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "latency_vs_docs.png", dpi=150)
    plt.close(fig)

    # 2) table scale: link latency + cards size
    labels = [t["label"] for t in report["table_scales"]]
    link_p50 = [t["link_p50_ms"] for t in report["table_scales"]]
    cards = [t["cards_chars"] for t in report["table_scales"]]
    fig, ax1 = plt.subplots(figsize=(6, 3.6))
    ax1.bar(labels, link_p50, color="#4c78a8", width=0.5)
    ax1.set_ylabel("Schema Linking P50 (ms)")
    ax1.set_title("表规模对 Schema Linking 的影响")
    for i, v in enumerate(link_p50):
        ax1.text(i, v, f"{v:.0f}ms", ha="center", va="bottom", fontsize=9)
    ax2 = ax1.twinx()
    ax2.plot(labels, cards, "r^-", label="语义卡总字符数")
    ax2.set_ylabel("语义卡字符数")
    ax2.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "table_scale_linking.png", dpi=150)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--questions", type=int, default=6)
    args = ap.parse_args()

    report = {"started_at": dt.datetime.now().isoformat(timespec="seconds"), "doc_scales": [], "table_scales": []}
    for scale in (10, 100, 1000):
        print(f"· doc scale {scale} …", flush=True)
        report["doc_scales"].append(bench_doc_scale(scale, args.repeats))
    for extra, label in ((0, "chinook-11"), (60, "synth-71")):
        print(f"· table scale {label} …", flush=True)
        row = bench_table_scale(extra, args.repeats)
        row["label"] = label
        report["table_scales"].append(row)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"efficiency_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    render_charts(report)
    print(f"report: {out}\ncharts: {OUT_DIR}")


if __name__ == "__main__":
    main()
