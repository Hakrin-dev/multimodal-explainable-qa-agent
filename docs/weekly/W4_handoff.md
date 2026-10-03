# W4 交接：B/C 阻塞项清单

> 时点：2026-10-01。A 侧 W4 已收口；B 已完成文档修复、目录恢复、BGE-M3 与 reranker，以下保留未完成项及交接状态。
> 以下保留原 B/C 交接项及最新状态；未完成项仍按**对 W4 产出的影响**降序。

## 一、阻塞演示场景（最高优先级）

### 1. #9 文档管理台闭环 —— 后端 ✅ / 前端待接入
- **C 主责**：前端文档管理台（上传 / 质量报告展示 / 修复触发 / 修复后问答）
- **B 主责**：修复能力（方向校正 / 去模糊 / 繁简转换）+ 上传 / 质量报告 / 修复 三个 API
- 现状：B 已完成上传、质量报告、原件/修复件预览、修复、Standard OCR、简繁转换和 ChunkIR 闭环；**当前仅 C 管理台未接入**
- 依赖链：B 后端接口已解除阻塞 → C 管理台 UI 接入；完整场景仍维持 demo 9/10，直到前端完成

### 2. #7 简化版目录识别 —— ✅ 已完成
- **B 主责**：缺失视觉层级文档（`bad_missing_structure.pdf`）的 H1/H2 恢复
- 现状：`bad_missing_structure.pdf` 已恢复文档标题及 6 个 H1；旋转扫描件方向校正、OCR 与切片也已闭环
- 验收：目录恢复和坏文档相关专项测试已纳入全量回归

## 二、决赛扩展（次高优先级）

### 3. BGE-M3 + reranker 部署（GPU）—— ✅ 已完成
- 已完成 BGE-M3 1024 维独立数据库验证、GPU embedding、`bge-reranker-v2-m3` Cross-Encoder 精排和消融脚本
- 验收：BGE-M3 吞吐约 1318 texts/s；Top-20 rerank P50 约 12 ms；双模型同驻峰值约 4.37 GiB
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

### 9. B 侧 OCR 兜底与召回短板 —— Standard GPU 已改善
- PaddleOCR CPU 兜底未接入
- 模糊繁体扫描件经强增强 + MinerU Standard GPU 达到事实命中 `4/5`、平均逐行相似度 `0.980`，并生成 `7 blocks / 1 chunk`；仍保留人工复核标记

## 四、A 侧已交付（供 B/C 依赖的接口面）

| 能力 | 位置 / 接口 |
|---|---|
| 跨源五类型 | kernel `_hybrid` DAG（plan→subtask→fuse）；intercept 路由 `_is_formula_question` / `_is_multidoc_compare` |
| 工具集 | `nl2sql` / `rag_search` / `db_lookup_entity` / `formula_eval`（自包含：resolve→三通道绑参→dual_eval） |
| 公式资产 | `kb_formula` 表（FormulaIR 契约）+ `FormulaEngine`（`app/formula/`） |
| Trace | 全链路事件树（`tool_call` 嵌套 param 子查询 + compute step），`GET /api/trace/{turn_id}` |
| 评测 | `eval/run_cross_source.py`（11 用例，多轮支持）、`eval/run_levels.py`（L0/L1/L2，含 `cross_source` family） |
| 环境加固 | `app/db/session.get_conn` 连接重试（WSL2 docker-proxy 5433 间歇断） |

> C 现在可直接接入 B 已完成的 `/api/docs/upload`、`/api/docs/{doc_id}/quality`、`/api/docs/{doc_id}/repair` 和`/api/docs/{doc_id}/pdf?version=...`。

## C 更新（2026-10-03）· 文档管理台已接入

- `frontend/src/api/documents.ts`：封装文档列表、上传、质量报告、修复和 PDF 预览接口。
- `frontend/src/components/DocumentConsole.vue`：完成文档列表、质量 JSON 展示、原件/修复件预览、修复触发和上传 PDF。
- `frontend/src/App.vue`：顶部新增“文档管理”入口，与对话工作台可切换。
- 前端兼容验收：`vue-tsc` 通过，Vitest **9/9** 通过（SSE 4 + 文档 API 5）；管理台直接消费 B 的现有 API，不改变 Trace/Citation 契约。
- 兼容性修正：`GET /api/docs` 现在同时列出 `data/docs_raw` 与 `data/docs_upload`，上传后刷新管理台不会丢失新文档。
- 体验收口：上传成功后管理台自动刷新、选中新文档并拉取质量报告，列表标识“知识库/已上传”来源。

场景 #9 的前后端闭环已完成；后续只需在真实部署环境补充截图和人工体验验收。
