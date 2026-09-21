"""W1 smoke: run the NL2SQL minimal loop over eval cases with a scripted mock.

No API key needed — the mock returns the expected generation output for each
case in order. This proves: rewrite → schema context → generate → validate →
execute → repair loop → summarize → trace, plus the eval runner mechanics.
Real-provider numbers come from the W1 model-selection eval (DeepSeek vs Qwen).
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.llm import MockProvider, get_llm_service  # noqa: E402

CANNED = [
    # (reference-consistent SQL for each eval case, then a summary line)
    "SELECT COUNT(*) FROM genre",
    "数据库中共有 25 种音乐曲风。",
    "SELECT name, bytes FROM track ORDER BY bytes DESC LIMIT 10",
    "以上是文件体积最大的 10 首曲目。",
    "SELECT firstname, lastname, city FROM customer WHERE country = 'Brazil' ORDER BY lastname",
    "巴西客户共 5 位。",
    "SELECT COUNT(*) FROM track WHERE unitprice > 1",
    "单价超过 1 美元的曲目共 213 首。",
    "SELECT DISTINCT country FROM customer ORDER BY country",
    "客户来自 24 个国家。",
    "SELECT firstname, lastname, hiredate FROM employee ORDER BY hiredate ASC LIMIT 1",
    "最早入职的员工已列出。",
    "SELECT name, milliseconds FROM track ORDER BY milliseconds DESC LIMIT 1",
    "时长最长的曲目已列出。",
    "SELECT name FROM playlist WHERE name ILIKE '%Music%' ORDER BY name",
    "名字包含 Music 的歌单已列出。",
    "SELECT SUM(total) FROM invoice",
    "所有发票总金额为 2328.6。",
    "SELECT genreid, COUNT(*) FROM track GROUP BY genreid ORDER BY COUNT(*) DESC, genreid",
    "各曲风曲目数已列出。",
    "SELECT COUNT(*) FROM customer WHERE country = 'USA'",
    "来自美国的客户共有 13 位。",
    # --- multi-table baseline (W3 #5 ablation: join-path injection vs this) ---
    "SELECT a.name, COUNT(*) FROM artist a JOIN album al ON al.artistid = a.artistid GROUP BY a.name ORDER BY COUNT(*) DESC, a.name LIMIT 10",
    "专辑数最多的 10 位艺术家已列出。",
    "SELECT t.name, SUM(il.quantity) FROM track t JOIN invoiceline il ON il.trackid = t.trackid GROUP BY t.name ORDER BY SUM(il.quantity) DESC, t.name LIMIT 10",
    "销量前 10 曲目已列出。",
    "SELECT e.firstname, e.lastname, SUM(i.total) FROM employee e JOIN customer c ON c.supportrepid = e.employeeid JOIN invoice i ON i.customerid = c.customerid GROUP BY e.employeeid, e.firstname, e.lastname ORDER BY SUM(i.total) DESC, e.lastname",
    "销售支持业绩已列出。",
]


def main() -> None:
    svc = get_llm_service()
    scripted: list[str] = []
    for gen_sql, summary in zip(CANNED[0::2], CANNED[1::2]):
        scripted.append(f"【分析】generated for case\n【SQL】\n```sql\n{gen_sql}\n```")
        scripted.append(summary)
    svc.register_mock(MockProvider(scripted=scripted))
    sys.argv = ["run_nl2sql", "--cases",
                str(BACKEND / "eval/cases/nl2sql_single_table.jsonl"),
                str(BACKEND / "eval/cases/nl2sql_multi_table.jsonl"),
                "--only-ids", "st-001,st-002,st-003,st-004,st-005,st-006,st-007,st-008,st-009,st-011,mt-001,mt-002,mt-017"]
    from eval.run_nl2sql import main as run_eval
    run_eval()


if __name__ == "__main__":
    main()
