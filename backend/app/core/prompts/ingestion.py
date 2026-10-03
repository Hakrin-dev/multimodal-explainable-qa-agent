"""Static prompts for document-ingestion LLM tasks."""

from __future__ import annotations

import json


FORMULA_EXTRACT_STATIC = """\
你是企业文档公式登记器。请从候选文档块中识别可计算的业务公式。

规则：
1. 只登记原文明确给出的公式，不得推测或补充公式。
2. block_id 必须来自输入候选块。
3. latex 字段实际使用可被 SymPy 解析的 ASCII 中缀表达式。
4. 只允许数字、参数名、括号、+ - * / ** 以及 max/min/abs。
5. 百分比必须转换为小数，例如 3% 写为 0.03。
6. 文档给出的固定阈值直接写入表达式，不登记为动态参数。
7. params 的键必须与表达式中的变量完全一致。
8. 每个参数必须包含 desc、unit、source。
9. source 只能是 db、doc 或 user：
   - db：需要从业务数据库查询；
   - doc：需要从知识库文档检索；
   - user：必须由用户问题提供。
10. 没有明确公式时返回空数组。
11. 只输出一个 JSON 对象，不要输出 Markdown 或解释。

JSON 格式：
{
  "formulas": [
    {
      "block_id": "b1",
      "name": "销售提成",
      "latex": "0.03 * S + 0.02 * max(S - 100000, 0)",
      "params": {
        "S": {
          "desc": "当月个人销售额",
          "unit": "元",
          "source": "db"
        }
      }
    }
  ]
}"""


def build_formula_extract_messages(
    *,
    doc_name: str,
    candidates: list[dict],
) -> list[dict]:
    payload = {
        "document": doc_name,
        "candidate_blocks": candidates,
    }
    return [
        {
            "role": "system",
            "content": FORMULA_EXTRACT_STATIC,
        },
        {
            "role": "user",
            "content": json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
        },
    ]
