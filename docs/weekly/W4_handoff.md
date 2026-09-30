# W4 交接：B/C 阻塞项清单

> 时点：2026-09-30（W4 A 侧收口）。A 侧 W4 全部完成（跨源五类型 + 澄清 + L1/L2 回归）。
> 以下为 B/C 主责的未完成项，按**对 W4 产出（10 场景 ≥8 可跑）的影响**降序。

## 一、阻塞演示场景（最高优先级）

### 1. #9 文档管理台闭环 —— 场景 9「上传坏文档→质量报告→修复前后对比→问答」
- **C 主责**：前端文档管理台（上传 / 质量报告展示 / 修复触发 / 修复后问答）
- **B 主责**：修复能力（方向校正 / 去模糊 / 繁简转换）+ 上传 / 质量报告 / 修复 三个 API
- 现状：B 已交付质量检测器 rules-v1（`ingestion/quality.py`）+ 复杂度评分 + MinerU 扫描件闭环 + 3 类坏文档资产（`data/docs_bad/`）；**修复能力与 API 未做，C 管理台未做**
- 依赖链：B 修复+API → C 管理台 UI。这是当前**唯一不可演示**的场景（demo 9/10）

### 2. #7 简化版目录识别 —— 坏文档目录重建
- **B 主责**：缺失视觉层级文档（`bad_missing_structure.pdf`）的 H1/H2 恢复
- 现状：B 自述「缺失视觉层级文档能够提取正文，但尚未恢复 H1/H2」；旋转扫描件已闭环
- 影响：坏文档演示的目录鲁棒性证据链不完整

## 二、决赛扩展（次高优先级）

### 3. BGE-M3 + reranker 部署（GPU）
- 当前 `bge-small-zh-v1.5`（512 维）；目标 BGE-M3（1024 维）+ `bge-reranker-v2-m3`
- 影响：RAG 多文档/规模化、决赛「效率与伸缩性证明」
- ⚠ 换维度需 drop + `--force` 重摄入（store 已内置检测）；**A 侧术语库向量需同步重算**（W3 曾做过 1024→512 对齐，方向反过来）

### 4. 忠实度自检（PLAN §4.3）
- LLM 逐句校验答案是否有引用支撑，不支撑则收敛重写
- 现状：引用生成已有，**自检未做**；直接关系 RAG 忠实度 10 分

### 5. 公式登记自动化（#6，决赛多公式）
- 现状：A 已代建 `kb_formula` 表 + seed 1 公式（`scripts/seed_formulas.py`，employee_handbook-f1 提成公式）
- **B 主责**：摄入期 LLM 从 blocks 抽 LaTeX→FormulaIR（参数 desc/source 标注准确）；A 的 `FormulaEngine` 直接消费

### 6. 知识库扩至 10 份（PLAN §6.1）
- 现状：4 份正常（`data/docs_raw/`）+ 3 坏文档（`data/docs_bad/` 隔离）
- 目标：10-15 份，与 Chinook 语义联动

## 三、C 侧评测 / 文档

### 7. 用例集 100%（PLAN §6.2 目标 ~140+）
- 现状 ~54：NL2SQL 单表 20 / 多表 11 / RAG 单文档 8 / 多轮 4 脚本 / **跨源 11（已超 PLAN 的 10）**
- 缺口：RAG 单文档 8→20、**RAG 多文档 15（0）**、**鲁棒性变体 30（0）**、**澄清 30（0）**、多轮 4→8 脚本、NL2SQL 多表 11→20

### 8. 技术文档（PLAN §13 W5 定稿）
- 设计报告（≤20 页，含核心亮点预览页）+ 技术实现说明书（≤30 页）
- W3 起骨架；W4 应推进；**每完成一个中级任务当周产出该章节初稿**（#1/#2/#4/#5/#9 章节 A 侧可直接引用 W3/W4 进度档）

### 9. B 侧 OCR 兜底与召回短板
- PaddleOCR CPU 兜底未接入
- 模糊繁体扫描件（`bad_blurred_traditional.pdf`）仅恢复标题与结论，**正文召回不足**

## 四、A 侧已交付（供 B/C 依赖的接口面）

| 能力 | 位置 / 接口 |
|---|---|
| 跨源五类型 | kernel `_hybrid` DAG（plan→subtask→fuse）；intercept 路由 `_is_formula_question` / `_is_multidoc_compare` |
| 工具集 | `nl2sql` / `rag_search` / `db_lookup_entity` / `formula_eval`（自包含：resolve→三通道绑参→dual_eval） |
| 公式资产 | `kb_formula` 表（FormulaIR 契约）+ `FormulaEngine`（`app/formula/`） |
| Trace | 全链路事件树（`tool_call` 嵌套 param 子查询 + compute step），`GET /api/trace/{turn_id}` |
| 评测 | `eval/run_cross_source.py`（11 用例，多轮支持）、`eval/run_levels.py`（L0/L1/L2，含 `cross_source` family） |
| 环境加固 | `app/db/session.get_conn` 连接重试（WSL2 docker-proxy 5433 间歇断） |

> 建议：C 的前端在文档管理台里复用 `GET /api/docs`（已有）；B 修复能力就绪后按「上传→assess→repair→re-ingest」四步暴露 API，接口形态对齐 `ingestion_ir.md` 契约。
