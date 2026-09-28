"""Usage-ledger analytics: cache economics for /api/usage & reports (§11.3).

Authority for the aggregation logic; CLI wrapper lives in eval/usage_report.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import Settings, get_settings


def _ledger_path(settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    p = Path(s.llm_usage_db)
    return p if p.is_absolute() else Path(__file__).resolve().parents[2] / p


def _price(provider: str, settings: Settings) -> tuple[float, float, float]:
    cfg = settings.provider_config(provider)
    return cfg.price_prompt, cfg.price_cached, cfg.price_completion


def collect(since: str | None = None, settings: Settings | None = None) -> list[dict]:
    path = _ledger_path(settings)
    if not path.exists():
        return []
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    q = ("SELECT ts, provider, model, purpose, prompt_tokens, cached_prompt_tokens,"
         " completion_tokens, cost_rmb, app_cache_hit, latency_ms FROM llm_usage")
    args: tuple = ()
    if since:
        q += " WHERE ts >= ?"
        args = (since,)
    rows = [dict(r) for r in con.execute(q + " ORDER BY ts", args)]
    con.close()
    return rows


def stats(since: str | None = None, settings: Settings | None = None) -> dict:
    """Aggregated cache economics — served by GET /api/usage (C's dashboard)."""
    s = settings or get_settings()
    rows = collect(since, s)
    real = [r for r in rows if not r["app_cache_hit"]]
    hits = [r for r in rows if r["app_cache_hit"]]

    pt = sum(r["prompt_tokens"] for r in real)
    cpt = sum(r["cached_prompt_tokens"] for r in real)
    ct = sum(r["completion_tokens"] for r in real)
    actual_cost = sum(r["cost_rmb"] for r in rows)
    real_cost = sum(r["cost_rmb"] for r in real)

    # hypothetical: no provider prefix cache (all prompt tokens at full price)
    no_pc_cost = 0.0
    for r in real:
        p, _pc, c = _price(r["provider"], s)
        no_pc_cost += (r["prompt_tokens"] / 1e6 * p) + (r["completion_tokens"] / 1e6 * c)
    # app-layer hits: would-have-been cost at full price (tokens logged on hit)
    hit_would_cost = 0.0
    for r in hits:
        p, _pc, c = _price(r["provider"], s)
        hit_would_cost += (r["prompt_tokens"] / 1e6 * p) + (r["completion_tokens"] / 1e6 * c)

    by_purpose: dict[str, dict] = {}
    for r in rows:
        d = by_purpose.setdefault(r["purpose"] or "(none)", {
            "calls": 0, "app_cache_hits": 0, "prompt_tokens": 0,
            "cached_prompt_tokens": 0, "completion_tokens": 0, "cost_rmb": 0.0})
        key = "app_cache_hits" if r["app_cache_hit"] else "calls"
        d[key] += 1
        d["prompt_tokens"] += r["prompt_tokens"]
        d["cached_prompt_tokens"] += r["cached_prompt_tokens"]
        d["completion_tokens"] += r["completion_tokens"]
        d["cost_rmb"] += r["cost_rmb"]

    daily: dict[str, float] = {}
    for r in rows:
        daily[r["ts"][:10]] = round(daily.get(r["ts"][:10], 0.0) + r["cost_rmb"], 4)

    return {
        "since": since,
        "total_calls": len(real),
        "app_cache_hits": len(hits),
        "prompt_tokens": pt,
        "prefix_cached_tokens": cpt,
        "prefix_cache_hit_rate": round(cpt / pt, 4) if pt else 0.0,
        "completion_tokens": ct,
        "actual_cost_rmb": round(actual_cost, 4),
        "cost_without_prefix_cache_rmb": round(no_pc_cost, 4),
        "prefix_cache_savings_rmb": round(no_pc_cost - real_cost, 4),
        "app_cache_savings_rmb": round(hit_would_cost, 4),
        "by_purpose": {k: {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                           for kk, vv in v.items()}
                       for k, v in sorted(by_purpose.items())},
        "daily_cost_rmb": dict(sorted(daily.items())),
    }
