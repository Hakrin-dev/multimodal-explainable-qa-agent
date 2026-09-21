"""Planner & fuse (PLAN §4.1/§4.5) — HYBRID question decomposition.

v1 (D3): LLM proposes a linear sub-task chain; the kernel executes it
sequentially, threading each result into the next step's context. The
Trace records the plan node with the full task list. W4 upgrades to a
true dependency DAG (parallel branches, fan-in fuse).
"""

from __future__ import annotations

import json
import re

from ..core.llm import LLMService, get_llm_service

PLAN_STATIC = """\
你是任务规划器。把用户的跨源问题分解为有序子任务，每个子任务只使用一种工具。

可用工具：
- nl2sql：查询结构化业务数据库（数字/名单/统计）
- rag_search：检索知识库文档（制度/手册/方法论）

规则：
1. 子任务顺序必须满足依赖（后面的子任务可以用到前面的结果）。
2. 每个子任务的 question 必须自包含、可直接执行；需要引用前序结果时用 {上一步结果} 占位。
3. 不要发明工具；不要超过 4 个子任务。

输出严格 JSON：
{"sub_tasks": [{"tool": "nl2sql|rag_search", "question": "..."}]}"""

FUSE_STATIC = """\
你是跨源融合生成器。根据多个子任务的结构化结果回答用户的原始问题。

规则：
1. 分区溯源：数据库事实标注来源为[数据库]，文档内容标注[文档名]；引用文档时给出文档名与页码。
2. 某个子任务失败或无结果时，如实说明该部分缺失，禁止编造。
3. 直接回答问题，简洁（3~6 句），先给结论再给依据。"""


def _extract_json(content: str) -> dict | None:
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        return None


class Planner:
    def __init__(self, llm: LLMService | None = None):
        self.llm = llm or get_llm_service()

    def plan(self, question: str, tool_inventory: str) -> list[dict]:
        """Returns [{tool, question}]; empty list = planning failed (caller
        may fall back to a single rag_search or honest failure)."""
        messages = [
            {"role": "system", "content": PLAN_STATIC + "\n\n【工具清单】\n" + tool_inventory},
            {"role": "user", "content": f"【用户问题】\n{question}"},
        ]
        resp = self.llm.chat(messages, temperature=0.0, max_tokens=400,
                             response_json=True, purpose="agent.plan")
        data = _extract_json(resp.content)
        tasks = []
        if data and isinstance(data.get("sub_tasks"), list):
            for t in data["sub_tasks"][:4]:
                if isinstance(t, dict) and t.get("tool") in ("nl2sql", "rag_search") \
                        and t.get("question"):
                    tasks.append({"tool": t["tool"], "question": str(t["question"])})
        return tasks


def build_fuse_messages(question: str, sub_results: list[dict]) -> list[dict]:
    parts = []
    for i, sr in enumerate(sub_results, 1):
        parts.append(f"[子任务{i}] 工具={sr['tool']} 问题={sr['question']}\n"
                     f"结果：{json.dumps(sr['result'], ensure_ascii=False, default=str)[:1500]}")
    return [
        {"role": "system", "content": FUSE_STATIC},
        {"role": "user", "content": "\n\n".join(parts) + f"\n\n【用户原始问题】\n{question}"},
    ]
