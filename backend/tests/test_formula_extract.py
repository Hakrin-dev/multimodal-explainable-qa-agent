"""FormulaIR automatic extraction tests."""

from __future__ import annotations

from types import SimpleNamespace

from app.ingestion.formula_extract import extract_formulas
from app.ingestion.ir import BlockIR, DocIR


class FakeLLM:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return SimpleNamespace(content=self.content)


def _doc(text: str) -> DocIR:
    return DocIR(
        doc_id="employee_handbook",
        name="Chinook 唱片员工手册",
        source_path="mem://employee_handbook",
        pages=1,
        blocks=[
            BlockIR(
                id="h1",
                page=1,
                type="heading",
                level=1,
                text="薪酬与提成",
            ),
            BlockIR(
                id="b1",
                page=1,
                type="paragraph",
                text=text,
            ),
        ],
    )


def test_extract_valid_formula() -> None:
    llm = FakeLLM(
        """{
          "formulas": [{
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
          }]
        }"""
    )
    doc = _doc(
        "月度提成 = 当月个人销售额 × 3% + "
        "超出 10 万元部分 × 2%。"
    )

    result = extract_formulas(doc, llm=llm)

    assert result.extracted == 1
    assert result.rejected == []
    assert len(llm.calls) == 1

    formula = doc.formulas[0]
    assert formula.id == "employee_handbook-f1"
    assert formula.page == 1
    assert formula.name == "销售提成"
    assert formula.breadcrumb == [
        "Chinook 唱片员工手册",
        "薪酬与提成",
    ]
    assert formula.params["S"]["source"] == "db"

    _, kwargs = llm.calls[0]
    assert kwargs["purpose"] == "rag.ingest"
    assert kwargs["response_json"] is True


def test_dangerous_expression_is_rejected() -> None:
    llm = FakeLLM(
        """{
          "formulas": [{
            "block_id": "b1",
            "name": "危险公式",
            "latex": "__import__('os').system('id')",
            "params": {}
          }]
        }"""
    )
    doc = _doc("计算公式 = 执行危险内容。")

    result = extract_formulas(doc, llm=llm)

    assert result.extracted == 0
    assert len(result.rejected) == 1
    assert doc.formulas == []


def test_unmatched_parameter_is_rejected() -> None:
    llm = FakeLLM(
        """{
          "formulas": [{
            "block_id": "b1",
            "name": "错误公式",
            "latex": "S * rate",
            "params": {
              "S": {
                "desc": "销售额",
                "unit": "元",
                "source": "db"
              }
            }
          }]
        }"""
    )
    doc = _doc("提成计算公式 = 销售额乘以比例。")

    result = extract_formulas(doc, llm=llm)

    assert result.extracted == 0
    assert len(result.rejected) == 1


def test_no_candidate_skips_llm() -> None:
    llm = FakeLLM('{"formulas": []}')
    doc = _doc("员工应当遵守公司规章制度。")

    result = extract_formulas(doc, llm=llm)

    assert result.candidates == 0
    assert result.extracted == 0
    assert llm.calls == []
    assert (
        doc.meta["formula_extraction"]["attempted"]
        is True
    )


def test_invalid_json_degrades_without_exception() -> None:
    llm = FakeLLM("not json")
    doc = _doc("销售提成计算公式为销售额的 3%。")

    result = extract_formulas(doc, llm=llm)

    assert result.extracted == 0
    assert result.error
    assert doc.formulas == []


def test_unknown_block_is_rejected() -> None:
    llm = FakeLLM(
        """{
          "formulas": [{
            "block_id": "invented",
            "name": "虚构公式",
            "latex": "S * 0.03",
            "params": {
              "S": {
                "desc": "销售额",
                "unit": "元",
                "source": "db"
              }
            }
          }]
        }"""
    )
    doc = _doc("销售提成 = 销售额 × 3%。")

    result = extract_formulas(doc, llm=llm)

    assert result.extracted == 0
    assert len(result.rejected) == 1


def test_valid_extraction_is_replace_ready() -> None:
    llm = FakeLLM(
        """{
          "formulas": [{
            "block_id": "b1",
            "name": "销售提成",
            "latex": "0.03 * S",
            "params": {
              "S": {
                "desc": "销售额",
                "unit": "元",
                "source": "db"
              }
            }
          }]
        }"""
    )
    doc = _doc("销售提成 = 销售额 × 3%。")

    result = extract_formulas(doc, llm=llm)

    assert result.completed is True
    assert result.replace_ready is True
    assert doc.meta["formula_extraction"]["replace_ready"] is True


def test_failed_extraction_must_not_replace_existing_rows() -> None:
    llm = FakeLLM("not json")
    doc = _doc("销售提成 = 销售额 × 3%。")

    result = extract_formulas(doc, llm=llm)

    assert result.completed is False
    assert result.replace_ready is False
    assert doc.meta["formula_extraction"]["replace_ready"] is False


def test_rejected_formula_must_not_replace_existing_rows() -> None:
    llm = FakeLLM(
        """{
          "formulas": [{
            "block_id": "b1",
            "name": "危险公式",
            "latex": "__import__('os')",
            "params": {}
          }]
        }"""
    )
    doc = _doc("销售提成计算公式 = 危险内容。")

    result = extract_formulas(doc, llm=llm)

    assert result.completed is True
    assert result.rejected
    assert result.replace_ready is False


def test_empty_formula_response_must_not_replace_existing_rows() -> None:
    llm = FakeLLM('{"formulas": []}')
    doc = _doc("销售提成计算公式为销售额的 3%。")

    result = extract_formulas(doc, llm=llm)

    assert result.completed is True
    assert result.extracted == 0
    assert result.replace_ready is False
    assert doc.meta["formula_extraction"]["replace_ready"] is False