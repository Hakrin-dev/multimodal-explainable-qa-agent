"""NL2SQL prompt templates — three-layer structure (PLAN §11.3).

Layer A (static):   role + rules + output format.  FROZEN ORDER from W2 on —
                    provider prefix-cache hits depend on it; append-only.
Layer B (semi-static): schema context + term samples. Stable per DB/doc-set.
Layer C (dynamic):  user question (+ history + repair feedback). Always last.
"""

from __future__ import annotations

# ---------------------------------------------------------------- layer A --
# 静态层：跨请求不变。W2 冻结后只允许追加，不允许改序/改写。
STATIC_RULES = """\
你是一个严谨的 PostgreSQL 数据分析助手，负责把用户的自然语言问题转换成一条只读 SQL 查询。

规则：
1. 只生成一条 SELECT 语句（可含 JOIN / GROUP BY / 子查询），禁止任何写入或 DDL。
2. 只使用【数据库模式】中出现的表和列；不要编造任何表名、列名或函数。
3. 表之间的关联只能使用模式中给出的外键关系；如需跨表，优先使用显式给出的外键路径。
4. 聚合查询必须显式 GROUP BY；ORDER BY 方向要符合问题的语义（"最高/最多" = DESC，"最低/最少" = ASC）。
5. 输出前先自检：列名拼写、表名拼写、外键列是否匹配。
6. 若问题缺少必要条件导致无法确定查询（如"增长率"未指定时间范围），不要猜测，在【分析】中说明缺失的要素并输出 SQL: None。

输出格式（严格遵守，不要输出其他内容）：
【分析】<一句话：目标表、过滤条件、聚合方式、排序依据>
【SQL】
```sql
<一条 SELECT 语句；若无法生成则写 None>
```"""

# ------------------------------------------------------------- layer B/C --


def build_schema_block(schema_context: str) -> str:
    """Semi-static block: compressed schema DDL + enum samples."""
    return f"【数据库模式】\n{schema_context}"


def build_question_block(question: str, history: list[dict] | None = None,
                         feedback: str | None = None) -> str:
    """Dynamic block: always last. `feedback` carries repair-loop errors."""
    parts = []
    if history:
        turns = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in history[-6:])
        parts.append(f"【对话历史】\n{turns}")
    parts.append(f"【问题】\n{question}")
    if feedback:
        parts.append(f"【上一次尝试的失败反馈（请修正）】\n{feedback}")
    return "\n\n".join(parts)


def build_messages(schema_context: str, question: str,
                   history: list[dict] | None = None,
                   feedback: str | None = None) -> list[dict]:
    """Assemble the full message list: static -> semi-static -> dynamic."""
    return [
        {"role": "system", "content": STATIC_RULES + "\n\n" + build_schema_block(schema_context)},
        {"role": "user", "content": build_question_block(question, history, feedback)},
    ]


SUMMARIZE_SYSTEM = """\
你是数据分析解说员。根据给定的 SQL 查询结果，用中文给出简洁的结论（2~4 句）：
- 直接回答问题中的数字/实体，不要复述全部数据；
- 如结果为空，说明"查询未返回数据"，不要编造；
- 不要使用 markdown 列表或标题。"""


def build_summarize_messages(question: str, sql: str,
                             columns: list[str], rows: list[list]) -> list[dict]:
    preview = rows[:20]
    return [
        {"role": "system", "content": SUMMARIZE_SYSTEM},
        {"role": "user", "content": (
            f"用户问题：{question}\n\nSQL：\n{sql}\n\n"
            f"列：{columns}\n数据（最多展示20行）：\n{preview}"
        )},
    ]
