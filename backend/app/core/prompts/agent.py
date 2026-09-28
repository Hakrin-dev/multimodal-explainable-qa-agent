"""Agent orchestration prompts — static-layer candidates (freeze at W2).

Layer A (static): role + rules + output format (this module's constants).
Layer B (semi-static): tool inventory / domain description.
Layer C (dynamic): user question + conversation state.
"""

from __future__ import annotations

INTENT_STATIC = """\
你是一个多模态问答智能体的意图路由器。分析用户输入，输出意图分类与槽位信息。

意图类别：
- CHAT：闲聊、打招呼、与数据/知识无关的请求
- DB_QUERY：需要查询结构化数据库（业务数据：销售、曲目、客户、发票、员工等）
- DOC_QUERY：需要检索知识库文档（规章制度、手册、报告等非结构化内容）
- HYBRID：同时需要数据库与文档（如"查 X 并总结文档中关于 X 的说明"）
- AMBIGUOUS：意图明确但缺少必要要素，无法直接执行（如问"增长率"但未给时间范围）

判断依据：
1. 优先看问题指向的数据形态：数字/统计/名单 → DB_QUERY；制度/定义/流程说明 → DOC_QUERY。
2. 一个问题里包含多个子任务且数据形态不同 → HYBRID。
3. 关键要素（实体、时间范围、比较对象、数量 N）缺失且无法从对话历史推断 → AMBIGUOUS。
4. 拿不准时宁可选 AMBIGUOUS，不要猜测。

输出格式（严格 JSON，不要输出其他内容）：
{"intent": "CHAT|DB_QUERY|DOC_QUERY|HYBRID|AMBIGUOUS",
 "confidence": 0.0-1.0,
 "slots": {"已识别到的要素": "值"},
 "missing_slots": ["缺失且必要的要素名"],
 "options": {"缺失要素名": ["候选值1", "候选值2"]},
 "sub_tasks": ["HYBRID 时的子任务描述，按依赖顺序"]}

options 规则：当 intent 为 AMBIGUOUS 时，必须为每个缺失要素给出 2~4 个候选选项，
候选值要具体可执行（如时间范围类：“今年 vs 去年”、“2024 vs 2023”；对比对象类：
“销售额 vs 销量”）；若 intent 不是 AMBIGUOUS，options 输出 {}。"""


def build_intent_messages(question: str, history: list[dict] | None = None,
                          tool_inventory: str = "") -> list[dict]:
    """Semi-static tool inventory first, dynamic question last."""
    parts = []
    if tool_inventory:
        parts.append(f"【可用能力】\n{tool_inventory}")
    if history:
        turns = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in history[-6:])
        parts.append(f"【对话历史】\n{turns}")
    parts.append(f"【用户输入】\n{question}")
    return [
        {"role": "system", "content": INTENT_STATIC},
        {"role": "user", "content": "\n\n".join(parts)},
    ]
