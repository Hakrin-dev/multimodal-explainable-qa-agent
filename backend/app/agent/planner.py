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
你是任务规划器。把用户的跨源问题分解为子任务 DAG，每个子任务只使用一种工具。

可用工具：
- nl2sql：查询结构化业务数据库（数字/名单/统计）
- rag_search：检索知识库文档（制度/手册/方法论）

规则：
1. 相互独立的子任务不要人为串联——它们会被并行执行；只有真正依赖前序结果的才建立依赖。
2. 每个子任务的 question 必须自包含；需要引用其它子任务结果时用占位符 {tN.result}（N 为该子任务 id）。
3. 不要发明工具；不要超过 4 个子任务。

输出严格 JSON：
{"sub_tasks": [{"id": "t1", "tool": "nl2sql|rag_search", "question": "...", "depends_on": []}]}

depends_on 填该子任务依赖的前序子任务 id 列表（无依赖则空数组；question 中未使用占位符的任务一般无依赖）。"""

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
        """Returns [{id, tool, question, depends_on}]; empty list = planning failed.

        Backward compat: legacy linear plans without ids/depends_on get ids
        t1..tN; {上一步结果} placeholders imply a chain dependency.
        """
        messages = [
            {"role": "system", "content": PLAN_STATIC + "\n\n【工具清单】\n" + tool_inventory},
            {"role": "user", "content": f"【用户问题】\n{question}"},
        ]
        resp = self.llm.chat(messages, temperature=0.0, max_tokens=400,
                             response_json=True, purpose="agent.plan")
        data = _extract_json(resp.content)
        tasks: list[dict] = []
        if data and isinstance(data.get("sub_tasks"), list):
            for i, t in enumerate(data["sub_tasks"][:4], 1):
                if not (isinstance(t, dict) and t.get("tool") in ("nl2sql", "rag_search")
                        and t.get("question")):
                    continue
                tid = str(t.get("id") or f"t{i}")
                q = str(t["question"])
                deps = [str(d) for d in t.get("depends_on") or [] if str(d) != tid]
                # legacy placeholder implies chain dependency on the previous task
                if "{上一步结果}" in q and i > 1 and not deps:
                    deps = [f"t{i - 1}"]
                tasks.append({"id": tid, "tool": t["tool"], "question": q,
                              "depends_on": deps})
        # normalize: drop deps pointing at unknown ids
        known = {t["id"] for t in tasks}
        for t in tasks:
            t["depends_on"] = [d for d in t["depends_on"] if d in known]
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
