"""Seed kb_formula with the employee handbook commission formula (W4-D1).

FormulaIR contract: docs/contracts/ingestion_ir.md §FormulaIR.
Source text (kb_doc_content.py 销售提成计算办法):
  "月度提成 = 当月个人销售额 × 3% + 超额部分 × 2%，超额部分指销售额超出
   月度销售目标 10 万元的部分。"

LaTeX is registered in SymPy-ready form so the W4-D1 minimal loop can eval
without an LLM LaTeX→SymPy pass; the conversion path is reserved for general
LaTeX in #6b.

Run: cd backend && python scripts/seed_formulas.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.formula import store  # noqa: E402
from app.ingestion.ir import FormulaIR  # noqa: E402

FORMULAS = [
    FormulaIR(
        id="employee_handbook-f1",
        doc_id="employee_handbook",
        page=1,
        breadcrumb=["Chinook 唱片员工手册", "薪酬与提成", "销售提成计算办法"],
        name="销售提成",
        latex="0.03 * S + 0.02 * max(S - 100000, 0)",
        params={
            "S": {
                "desc": "销售支持专员当月个人销售额",
                "unit": "元",
                "source": "db",
            },
        },
    ),
]


def main() -> None:
    for f in FORMULAS:
        store.upsert(f)
        print(f"  · {f.id}  {f.name}  latex={f.latex}")
    rows = store.list_all()
    print(f"kb_formula: {len(rows)} registered")


if __name__ == "__main__":
    main()
