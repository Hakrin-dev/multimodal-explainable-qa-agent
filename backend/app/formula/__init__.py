"""Formula computation engine (中级 #6, W4-D1).

PLAN §4.4 + 契约 FormulaIR (docs/contracts/ingestion_ir.md). 最小闭环：
公式登记 (kb_formula) → LaTeX→SymPy → 参数三通道绑定 (db/doc/user) →
双通道校验 (SymPy 符号求值 vs 受限 namespace Python eval) → 计算步骤进 Trace.

Cross-source type ② (Doc→DB 参数查询型): 公式来自文档，参数值来自 DB。
Demo scenario #7: "按员工手册提成公式，Jane 本月提成多少".
"""

from __future__ import annotations

from .engine import FormulaEngine, FormulaResult

__all__ = ["FormulaEngine", "FormulaResult"]
