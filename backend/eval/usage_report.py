"""Usage & cache-economics report CLI (PLAN W2 产出：缓存命中率首次统计).

Aggregation logic lives in app/core/usage.py (authority, also served by
GET /api/usage). This wrapper renders markdown / prints to stdout.

Usage: python eval/usage_report.py [--since 2026-09-28] [--md out.md]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.usage import stats  # noqa: E402


def render_md(s: dict) -> str:
    lines = [
        "# LLM 用量与缓存经济报告", "",
        f"- 生成时间：{dt.datetime.now().isoformat(timespec='seconds')}"
        + (f"（统计起点：{s['since']}）" if s["since"] else "（全量）"),
        "",
        "## 总览", "",
        f"- 真实调用：{s['total_calls']} 次；应用层缓存命中：{s['app_cache_hits']} 次",
        f"- Prompt tokens：{s['prompt_tokens']:,}（其中**前缀缓存命中 {s['prefix_cached_tokens']:,}，"
        f"命中率 {s['prefix_cache_hit_rate']:.1%}**）；Completion tokens：{s['completion_tokens']:,}",
        f"- 实际成本：**¥{s['actual_cost_rmb']}**",
        f"- 若无提供商前缀缓存：¥{s['cost_without_prefix_cache_rmb']} → "
        f"**前缀缓存已节省 ¥{s['prefix_cache_savings_rmb']}**",
        f"- 应用层缓存命中折算节省（估算）：¥{s['app_cache_savings_rmb']}",
        "",
        "## 按 purpose 分解", "",
        "| purpose | 真实调用 | 缓存命中 | prompt tokens | 前缀命中 | completion | 成本(¥) |",
        "|---|---|---|---|---|---|---|",
    ]
    for k, v in s["by_purpose"].items():
        rate = f"{v['cached_prompt_tokens'] / v['prompt_tokens']:.0%}" if v["prompt_tokens"] else "-"
        lines.append(f"| {k} | {v['calls']} | {v['app_cache_hits']} | {v['prompt_tokens']:,} "
                     f"| {v['cached_prompt_tokens']:,} ({rate}) | {v['completion_tokens']:,} "
                     f"| {v['cost_rmb']:.4f} |")
    lines += ["", "## 每日成本（技术文档曲线素材）", "", "| 日期 | 成本(¥) |", "|---|---|"]
    for d, c in s["daily_cost_rmb"].items():
        lines.append(f"| {d} | {c:.4f} |")
    lines += ["", "> 注：前缀缓存命中数仅 DeepSeek 显式上报；Qwen 为隐式缓存不上报（其行计 0）。"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="ISO date/datetime filter")
    ap.add_argument("--md", default=None, help="also write markdown report to this path")
    args = ap.parse_args()

    s = stats(since=args.since)
    print(render_md(s))
    if args.md:
        out = Path(args.md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_md(s), encoding="utf-8")
        print(f"\nreport written: {out}")


if __name__ == "__main__":
    main()
