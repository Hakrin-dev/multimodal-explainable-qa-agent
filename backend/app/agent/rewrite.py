"""Multi-turn coreference rewrite (PLAN §4.6 会话与记忆 · A 主责).

Turns follow-up questions into standalone ones before intent/routing:
    "那 2023 呢" + [历史: 销售额增长率 2024vs2023] → "2023 年的销售额增长率是多少"

Design:
- Only fires when history exists (first turn is already standalone)
- LLM one-shot, strict JSON, purpose=agent.rewrite (registered family)
- Robust: any parse failure returns the original question unchanged
- The rewrite 对照 goes to Trace (detail.rewrites) + TurnResult.rewritten
  (前端展示改写对照 = 可解释性素材, PLAN §4.6)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..core.llm import LLMService, get_llm_service

REWRITE_STATIC = """\
你是对话改写器。把用户的最新输入改写成不依赖上下文、可独立执行的完整问题。

规则：
1. 只补全指代（他/她/它/该/这）与省略的成分，不增加新信息、不改变意图。
2. 若最新输入已经是完整问题，原样返回。
3. 从对话历史中取实体与时间等要素，不要编造历史中不存在的要素。
4. 输出严格 JSON：{"question": "改写后的问题", "changed": true|false}"""

_TRIVIAL = re.compile(r"^(好|嗯|哦|是的?|不是|继续|谢谢)[呀啊!！。。\s]*$")


@dataclass
class RewriteOutcome:
    question: str
    original: str
    changed: bool = False
    detail: dict = field(default_factory=dict)

    @property
    def rewrites(self) -> list[dict]:
        return [{"before": self.original, "after": self.question}] if self.changed else []


def _extract_json(content: str) -> dict | None:
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        return None


class CoreferenceRewriter:
    def __init__(self, llm: LLMService | None = None):
        self.llm = llm or get_llm_service()

    def rewrite(self, question: str, history: list[dict] | None = None) -> RewriteOutcome:
        # standalone by construction: no history, or trivial acknowledgements
        if not history or len(history) < 2 or _TRIVIAL.match(question.strip()):
            return RewriteOutcome(question=question, original=question)

        turns = "\n".join(f"{m['role']}: {str(m['content'])[:200]}"
                          for m in history[-6:])
        messages = [
            {"role": "system", "content": REWRITE_STATIC},
            {"role": "user", "content": f"【对话历史】\n{turns}\n\n【最新输入】\n{question}"},
        ]
        try:
            resp = self.llm.chat(messages, temperature=0.0, max_tokens=200,
                                 response_json=True, purpose="agent.rewrite")
            data = _extract_json(resp.content)
            if data and isinstance(data.get("question"), str) and data["question"].strip():
                rewritten = data["question"].strip()
                changed = bool(data.get("changed")) or rewritten != question
                if changed and rewritten != question:
                    return RewriteOutcome(question=rewritten, original=question,
                                          changed=True, detail={"rewrites": [
                                              {"before": question, "after": rewritten}]})
        except Exception:  # noqa: BLE001 — rewrite must never break a turn
            pass
        return RewriteOutcome(question=question, original=question)
