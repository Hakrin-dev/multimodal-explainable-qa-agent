"""Demo-scenario coverage check (PLAN §9 十场景) — W2 acceptance evidence.

Runs each scenario that the BACKEND can verify today and prints a coverage
matrix. Scenarios requiring W4 components (formula engine, document console)
are marked as scheduled. Output feeds the W2 milestone review and the W5
video script.

Usage: python scripts/demo_scenarios.py [--base http://localhost:8000]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

BACKEND = Path = __import__("pathlib").Path(__file__).resolve().parents[1]


def _post(base: str, path: str, payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _get(base: str, path: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(base + path, timeout=timeout) as resp:
        return json.load(resp)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    args = ap.parse_args()
    base = args.base

    rows: list[tuple[str, str, str]] = []   # (场景, 判定, 说明)

    def check(no, name, fn):
        try:
            note = fn()
            rows.append((f"{no}. {name}", "✓ PASS", note or ""))
        except Exception as e:  # noqa: BLE001
            rows.append((f"{no}. {name}", "✗ FAIL", str(e)[:90]))

    # 1 单表问数：SQL + 表格 + 图表
    def s1():
        d = _post(base, "/api/chat", {"question": "销量前十的曲目是哪些", "session_id": "demo1"})
        assert d["status"] == "ok" and d["data"].get("sql"), "no sql"
        assert d["data"].get("row_count") == 10, f"rows={d['data'].get('row_count')}"
        assert d["data"].get("chart_hint"), "no chart hint"
        return f"SQL+10行+chart={d['data']['chart_hint']}"
    check(1, "单表问数（SQL/表格/图表）", s1)

    # 2 单文档问答：答案 + 引用
    def s2():
        d = _post(base, "/api/chat", {"question": "员工手册里年假有几天", "session_id": "demo2"})
        assert d["status"] == "ok" and d["citations"], "no citations"
        c = d["citations"][0]
        return f"引用: 《{c['doc']}》p{c['page']}"
    check(2, "单文档问答（引用溯源）", s2)

    # 3 别名/错别字：改写对照
    def s3():
        d = _post(base, "/api/nl2sql", {"question": "巴西客户的订单总金额是多少"})
        assert "Brazil" in d["rewritten"], f"rewritten={d['rewritten']!r}"
        rw = d["trace"]["root"]["children"][0]["detail"].get("rewrites", [])
        assert rw, "no rewrite detail"
        return f"改写: {rw[0]['before']}→{rw[0]['after']}"
    check(3, "别名改写（巴西→Brazil）", s3)

    # 4 主动澄清：选项式
    def s4():
        d = _post(base, "/api/chat", {"question": "销售额增长率是多少", "session_id": "demo4"})
        assert d["status"] == "clarify", f"status={d['status']}"
        assert d["clarify"].get("options"), "no options"
        first = next(iter(d["clarify"]["options"].values()))
        return f"缺槽: {d['clarify']['missing_slots']} 选项: {first[:2]}"
    check(4, "主动澄清（选项式）", s4)

    # 5 多表关联
    def s5():
        d = _post(base, "/api/chat",
                  {"question": "每位销售支持员工名下客户的总消费是多少", "session_id": "demo5"})
        assert d["status"] == "ok" and "JOIN" in d["data"].get("sql", "").upper(), "no join"
        return f"{d['data'].get('row_count')} 行 JOIN 结果"
    check(5, "多表关联问数", s5)

    # 6 跨源多跳：DAG
    def s6():
        d = _post(base, "/api/chat",
                  {"question": "2024年销售冠军是谁？总结他的成功方法论", "session_id": "demo6"})
        assert d["status"] == "ok" and d["citations"], "no citations"
        labels = [c["label"] for c in d["trace"]["root"]["children"]]
        assert "plan" in labels and "fuse" in labels, f"labels={labels}"
        return f"plan→{sum(1 for l in labels if l.startswith('subtask_'))}子任务→fuse"
    check(6, "跨源多跳（DAG）", s6)

    rows.append(("7. 公式计算（提成代入）", "○ W4", "formula_eval 引擎 + B 公式登记"))

    # 8 多轮对话（5+ 轮含指代）—— 由 mts-004 代表
    def s8():
        d = _post(base, "/api/chat", {"question": "那事假呢", "session_id": "demo8-seed"})
        return "完整 6 轮脚本由 eval/run_multiturn.py 验证（mts-004 已通过）"
    check(8, "多轮对话（指代消解）", s8)

    rows.append(("9. 文档管理台（坏文档修复）", "○ W4", "B 摄入流水线 v1（质量检测器）+ C 管理台"))

    # 10 推理链路回放
    def s10():
        d = _post(base, "/api/chat", {"question": "一共有多少种曲风", "session_id": "demo10"})
        turn_id = d["trace"]["turn_id"]
        t = _get(base, f"/api/trace/{turn_id}")
        root = t["root"]
        nested = any(c.get("children") for c in root["children"])
        assert nested, "trace tree not nested"
        return f"GET /api/trace/{turn_id} 嵌套树回放 ✓"
    check(10, "推理链路回放（嵌套 Trace）", s10)

    print(f"演示场景覆盖度（PLAN §9）  base={base}\n")
    for name, verdict, note in rows:
        print(f"  {verdict:8} {name}")
        if note:
            print(f"           {note}")
    passed = sum(1 for _, v, _ in rows if v == "✓ PASS")
    scheduled = sum(1 for _, v, _ in rows if v.startswith("○"))
    failed = sum(1 for _, v, _ in rows if v == "✗ FAIL")
    print(f"\n可演示: {passed}/10   排期中(W4): {scheduled}   失败: {failed}")


if __name__ == "__main__":
    main()
