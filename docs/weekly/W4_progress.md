# W4 进度（10/12-10/18 计划周 · A 线 9/30 提前开工 W4-D1）

> A 角色按 PLAN §13 W4 排期主攻：跨源多跳（规划器 + 2 种类型演示级）+ 澄清基础版。
> 澄清基础版（槽位矩阵 + 选项式 + 回填续查）已于 W2-D2/D4 交付；HYBRID DAG 规划器
> + 跨源类型①（DB→Doc 实体档案型：冠军方法论）已于 W2-D3 交付。W4-D1 攻跨源类型②。

## D1（9/30）· A 完成 ✅ —— 公式计算引擎最小闭环（#6，跨源类型② Doc→DB 参数查询型）

| 项 | 状态 | 关键结果 |
|---|---|---|
| `kb_formula` 表 + seed 脚本 | ✅ | FormulaIR 契约 §3 落地（A 代建，B 未做登记）；`employee_handbook-f1` 销售提成公式入库（latex=`0.03*S+0.02*max(S-100000,0)`，S source=db） |
| `FormulaEngine`（`app/formula/`） | ✅ | resolve（关键词匹配 kb_formula）→ bind_params（db/doc/user 三通道，§4.4 6a）→ latex_to_sympy（parse_expr + max/min/abs 映射 + forbidden token 守卫）→ dual_eval（双通道：SymPy 符号求值 vs 受限 namespace Python eval，交叉校验）→ steps 进 Trace |
| DB 参数通道（跨源类型②核心） | ✅ | LLM 生成参数子问题（强制保留原问题人名、去掉"当月/本月"无法查时间词、改查累计总额）→ 嵌套 nl2sql tool_call → 取值优先 summary 底线数值（避免 count/sum 误读）fallback rows |
| `formula_eval` 工具接线 | ✅ | `tools._tool_formula_eval` 替换 W4 stub；自包含单工具闭环（Trace 嵌套 param 子查询 + compute step） |
| kernel 关键词 intercept 路由 | ✅ | 问题匹配 `提成.*公式|公式.*计算|公式.*多少` 时路由到 formula_eval，绕过 HYBRID planner 不确定性；**intent prompt 冻结文本零改动**（W2-D1 冻结 v1.0 守住） |
| 演示场景 #7（PLAN §9） | ✅ | "按员工手册提成公式 Jane 的提成是多少" → 提成=¥24.99（S=833.04 从 DB 查 Jane 名下客户消费总额，0.03×833.04）steps=4 |
| 测试 `test_formula.py` | ✅ | 9 项（latex_to_sympy/dual_eval/forbidden/coerce/store roundtrip/resolve/e2e db param/no formula/双通道不一致降级） |
| 全量回归 | ✅ | **155 passed**（146 + 9 formula） |
| 演示覆盖度 | ✅ | **9/10 可演示、0 失败**（+1 公式计算；W2-D4 达 8/10，W4-D1 达 9/10，仅余 #9 文档管理台依赖 C） |

### 跨源类型② 实测链路（演示场景 #7）

```
问题：按员工手册提成公式 Jane 的提成是多少
  → intent=HYBRID（LLM），formula_intercept 路由 formula_eval
  → resolve: employee_handbook-f1（"销售提成"匹配）
  → bind_params: S source=db
      LLM 生成子问题「Jane 的累计个人销售额是多少？」
      → 嵌套 nl2sql tool_call → SQL → Jane 名下客户 SUM(Invoice.Total)=833.04
      → 取 summary 底线 833.04
  → dual_eval: 0.03*833.04 + 0.02*max(833.04-100000,0)
      SymPy 通道 = 24.9912，eval 通道 = 24.9912，一致 ✅
  → steps: 公式 / S←DB查询=833.04 / 代入 / =24.9912
  → 答案：计算结果：24.9912（计算步骤 4 步）
```

### 设计决策与边界

1. **formula_eval 自包含单工具 vs HYBRID DAG 分解**：选单工具。HYBRID planner LLM 产 plan 不可靠（可能瞎编 formula_id/参数绑定）；单工具内部完成"找公式+绑参数+计算"，Trace 仍嵌套展示 param 子查询 + compute step，可解释性不丢。cross-source 属性（公式来自文档、参数来自 DB）在 Trace + citations（_db + doc breadcrumb）体现。
2. **LaTeX→SymPy 通用转换暂缓**：登记时 latex 已是 SymPy 风格（`0.03 * S + 0.02 * max(...)`），parse_expr 直接可用。通用 LaTeX（`\frac`/`^`）→ SymPy 的 LLM 转换路径留 #6b 决赛。
3. **双通道校验 = SymPy 符号求值 vs 受限 eval**：零 LLM 成本的两条独立计算路径交叉验证。PLAN §4.4 的"LLM 估算数量级比对"作为 `estimate()` 方法预留（#6b 增强），最小闭环用"值非负"合理性断言兜底。
4. **DB 参数取值优先 summary**：nl2sql 偶发把"累计销售额"误生成 count(*) 查询返回客户数（3）而非 sum（833.04），但 summary 自然语言表述含正确底线数值。取 summary 最大数值 token 避免 count/sum 误读，fallback rows。
5. **kernel intercept 不动 frozen prompt**：intent prompt（W2-D1 v1.0 冻结）文本零改动；formula 路由在 kernel 层关键词匹配，新增 `formula_intercept` STEP span 入 Trace（可解释）。

### 新增/变更文件

- `app/formula/__init__.py`、`app/formula/store.py`（kb_formula DDL+CRUD）、`app/formula/engine.py`（FormulaEngine）
- `scripts/seed_formulas.py`（登记 employee_handbook-f1）
- `tests/test_formula.py`（+9 测试）
- `app/agent/tools.py`：`_tool_formula_eval` stub → engine 接线；registry 描述更新
- `app/agent/kernel.py`：+`import re`、formula 关键词 intercept elif 分支、`_is_formula_question` helper
- `scripts/demo_scenarios.py`：场景 #7 从 ○W4 占位改为实跑 check

### 遗留与后续

- **B 公式登记自动化**：当前 seed 脚本手工登记 1 公式；B 的摄入期"LLM 从 blocks 抽 LaTeX→FormulaIR"（契约 §4 表格 ⬜ W2）仍未做——A 代建 kb_formula 表 + seed 1 公式（precedent: W1-D2+ A 代 B 建 KB docs v0）。决赛扩到多公式时需 B 在摄入期自动登记（参数 desc/source 标注准确）。
- **跨源类型③④⑤**（Doc→Doc 多文档对比 / DB 结果+Doc 背景解释综合 / 澄清后跨源续查）：W4-D2 起按演示需求推进，决赛覆盖 ≥5 种类型。
- **场景 #9 文档管理台**：依赖 C 管理台 + B 上传/质量报告/修复 API（B 摄入流水线 v1 已就绪 #9 检测器，C 管理台待做）。
