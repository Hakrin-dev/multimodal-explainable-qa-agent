# 多模态数据驱动的可解释精准问数/问答智能体（「问迹」）

“中国电子杯”第三届高校 ICT 产教融合创新大赛 · 赛题八（中电云计算）。
总体方案见 [PLAN.md](./PLAN.md)；赛题说明见 [multimodal-explainable-qa-agent-introduction.md](./multimodal-explainable-qa-agent-introduction.md)。

**一句话**：把结构化问数（NL2SQL）、文档问答（RAG）、跨源多跳与公式计算统一到自研事件驱动
Agent 内核，以 **Trace / 引用 / 可复现分级评测** 让每一步可解释、可审计、可回归。

## 快速启动

```bash
./deploy/quick_start.sh          # 一键：PG(pgvector) + Chinook 导入 + 术语库 + 知识库摄入 + 后端 + 前端 + 冒烟
```

- 后端 API：`http://localhost:8000` · 前端（Vue 3 + Naive UI）：`http://localhost:8090` · 数据库：`localhost:5433`（chinook/chinook123）
- 无需 API key 即可启动：默认 mock provider 可跑通全部流水线；真实模型在 `.env` 填
  `DEEPSEEK_API_KEY`/`QWEN_API_KEY` 并设 `LLM_PROVIDER=deepseek|qwen`（主力已定案 DeepSeek）
- 受限网络：`.env` 的 `DOCKER_REGISTRY_MIRROR`（镜像）/ `HF_ENDPOINT`（模型下载）已配置

## 功能一览

| 能力 | 说明 |
|---|---|
| 问数 NL2SQL | 术语改写→Schema Linking（71 表下 P50 160ms）→双步生成→sqlglot 校验→只读执行→自修复（≤3 轮）→空结果归因 |
| 问答 RAG | 查询改写（可选 HyDE）→混合检索（向量+BM25+RRF）→重排→引用生成→**逐句忠实度自检**（≤2 轮收敛重写） |
| 跨源多跳 | HYBRID 任务 DAG：依赖链/并行子任务/分区溯源，覆盖 5 类跨源问题（11/11 用例） |
| 公式计算（#6） | FormulaIR 自动登记（摄入期 LLM 抽取+校验）→三通道参数绑定（db/doc/user）→LaTeX→SymPy→双通道校验 |
| 澄清与多轮 | 槽位矩阵（外置配置+同义词词表）→选项式澄清→回填续查；指代消解改写（含改写对照展示） |
| 文档管理台 | 上传→质量报告→一键修复（方向/清晰度/OCR/繁简）→修复前后对比→修复后问答（清晰度实测 13x 提升） |
| 可解释性 | 全链路 Trace 树（时间线+DAG 双视图）· SQL/改写/引用对照 · PDF 页级定位 · 成本账本 |

## API 一览（`backend/app/main.py`）

| 类别 | 端点 |
|---|---|
| 对话编排 | `POST /api/chat`（JSON）· `POST /api/chat/stream`（SSE，契约见 docs/contracts/trace.md） |
| 单引擎 | `POST /api/nl2sql` · `POST /api/rag` |
| 文档管理 | `POST /api/docs/upload` · `GET /api/docs` · `GET /api/docs/{id}/quality` · `POST /api/docs/{id}/repair` · `GET /api/docs/{id}/pdf` |
| 可解释性/运维 | `GET /api/trace/{turn_id}` · `GET /api/trace` · `GET /api/usage`（含缓存经济 stats）· `GET /api/schema` · `GET /api/terms` · `GET /api/health` |

## 仓库结构

```
backend/
  app/
    core/       配置(.env) · LLM 抽象层(provider/缓存/记账) · Trace 模型 · Prompt 模板(11 家族,已冻结)
    db/         PG 会话(连接重试) · Schema 元数据/语义卡
    nl2sql/     改写(术语三级匹配) · Schema Linking(召回+精排+列级) · 校验/执行/空结果归因
    rag/        Embedding 抽象 · pgvector+BM25 · RRF · 重排 · 忠实度自检 · 查询改写/HyDE
    ingestion/  IR · 质量评估(#9) · 复杂度评分(#8) · MinerU/PaddleOCR 路由 · 修复工作流 · 公式抽取
    agent/      编排内核：意图/工具/HYBRID DAG/澄清/改写 · Trace+会话持久化 · SSE
    formula/    公式引擎：登记(store) · LaTeX→SymPy · 参数绑定 · 双通道校验
  eval/         分级评测 run_levels(L0/L1/L2) + 12 个用例文件 161 例 + 专项 runner(跨源/澄清/多轮/消融)
  scripts/      Chinook 导入 · 术语库抽取/别名生成 · 知识库 PDF 生成/摄入 · 基准语料/效率矩阵 · 冒烟
  tests/        255 个测试（单测 + 集成，含安全边界）
frontend/       Vue 3 + TS + Vite + Naive UI：对话流(SSE) · 数据卡片 · 引用 · 澄清按钮 · Trace 双视图 · 文档管理台
deploy/         docker-compose(pgvector+backend+frontend) · quick_start.sh
data/           db_raw(Chinook SQLite) · db_schema(DDL/术语种子) · docs_raw(10 份知识库) · docs_bad(3 份坏文档) · docs_bench(可再生成)
docs/
  contracts/    Trace JSON Schema · 摄入 IR · Prompt 静态层 · eval 用例格式（均已冻结，含变更记录）
  design/       设计报告与说明书（提交骨架）· Agent 内核设计
  milestones/   W1 demo 截图 · W5 效率矩阵（图+报告）
  onboarding/   B/C 上手文档
  weekly/       W1~W5 周报 · W4 验收报告 · 交接清单
```

## 评测与验证

```bash
cd backend
python eval/run_levels.py --level L0                 # 每类 5 例 smoke
python eval/run_levels.py --level L1                 # 全量、当前配置模型
python eval/run_levels.py --level L2 --providers deepseek,qwen --repeats 3   # 主备×3 重复
python eval/run_cross_source.py [--only-ids cs-011]  # 跨源专项
python scripts/demo_scenarios.py                     # 演示场景覆盖矩阵（10/10）
python scripts/bench_efficiency.py                   # 效率矩阵（3 文档规模 × 2 表规模，出图）
```

**最新基线（L2 全量，30 runs 零基础设施失败、三重复零方差）**：

| Family（用例数） | DeepSeek（主力） | Qwen（备胎） |
|---|---|---|
| NL2SQL（61） | **100%** | 83.6% |
| RAG 答案命中（41） | **100%**（检索 100%） | 93% |
| 多轮（8 脚本） | **8/8 · 34/34 轮** | 6/8 · 31/34 |
| 跨源多跳（11） | **11/11** | 9/11（路由 100%） |
| 澄清（30） | **30/30** | 7~8/30 |

结论：**路由与模型无关，生成质量与模型强相关**；效率矩阵（检索 10→1000 篇 P50 7→133ms、
Schema Linking 11→71 表 P50 55→160ms）与缓存经济（前缀命中 ~89%，节省 53% 成本）见
[docs/milestones/w5_efficiency](./docs/milestones/w5_efficiency/report.md) 与 `GET /api/usage`。

## 开发

```bash
# 后端
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q          # 255 passed（集成用例需 DB 已启动）
.venv/bin/python scripts/smoke_nl2sql.py      # NL2SQL 冒烟（mock 驱动）
.venv/bin/python scripts/smoke_rag.py         # RAG 检索冒烟（需嵌入模型）
.venv/bin/python eval/model_selection.py      # 选型评测（需 API key）

# 前端
cd frontend && pnpm install && pnpm test && pnpm build
pnpm dev                                       # http://localhost:5173，/api 代理到 8000
```

## 提交物对照（初赛三件套）

| 要求 | 现状 |
|---|---|
| 设计报告（≤20 页，含亮点预览页） | 骨架 `docs/design/design_report.md`（章节+证据入口已固定，C 牵头定稿） |
| 技术实现说明书（≤30 页） | 骨架 `docs/design/technical_implementation.md` |
| ZIP：源码 + schema 定义 + 知识库样本（≥10）+ 环境说明 + quick_start | ✅ `data/db_schema/chinook_pg.sql` · `data/docs_raw/` 10 份 · README 环境说明 · `deploy/quick_start.sh` |

## 里程碑

W1 地基/选型/双引擎最小闭环 → W2 编排内核/缓存/Prompt 冻结 → W3 中级任务 #1/#2/#4/#5 +
Schema Linking → W4 跨源五类型/公式引擎/L2 首测 → **W5 打磨：L2 终验 + 效率矩阵 + 文档/视频/打包**。
逐周状态：[W5_progress.md](./docs/weekly/W5_progress.md)（含 B/C W5 复验与 A 收官）；
W4 验收报告：[W4_acceptance.md](./docs/weekly/W4_acceptance.md)。
