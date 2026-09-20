# 多模态数据驱动的可解释精准问数/问答智能体

“中国电子杯”第三届高校 ICT 产教融合创新大赛 · 赛题八（中电云计算）。
总体方案见 [PLAN.md](./PLAN.md)；赛题说明见 [multimodal-explainable-qa-agent-introduction.md](./multimodal-explainable-qa-agent-introduction.md)。

## 快速启动

```bash
./deploy/quick_start.sh          # 一键：PG(pgvector) + Chinook 导入 + 术语库 + 后端 + 前端 + 冒烟
```

- 后端 API：`http://localhost:8000`（`/api/health`、`POST /api/nl2sql`、`/api/schema`、`/api/terms`、`/api/usage`）
- 前端占位页：`http://localhost:8090`（W2 替换为 Vue 3 + Naive UI）
- 数据库：`localhost:5433`（chinook/chinook123，库名 chinook）

无需 LLM API key 即可启动：默认 `LLM_PROVIDER=mock`，全部流水线可用脚本化 mock 驱动。
接入真实模型：编辑 `.env` 填入 `DEEPSEEK_API_KEY` / `QWEN_API_KEY`，设 `LLM_PROVIDER=deepseek|qwen`。

## 仓库结构

```
backend/
  app/
    core/          配置(.env 驱动) · LLM 抽象层(provider切换/响应缓存/用量记账) · Trace 数据模型 · Prompt 模板
    db/            PG 会话 · Schema 元数据读取（语义卡原料）
    nl2sql/        问数流水线：改写(术语链接) → 生成 → sqlglot 校验 → 只读执行 → 自修复 → 总结
    agent/ rag/ …  W2 起填充（编排内核 / RAG 引擎）
  scripts/         Chinook SQLite→PG 转换导入 · 术语库抽取 · 冒烟
  eval/            用例集(单表 10 + 多表 20 + 选型 10) · 跑分 runner · 用例预检 · LLM 选型评测(D1)
  tests/           单测(27) + 集成测试(需 DB)
frontend/          占位（W2: Vue 3 + TS + Vite + Naive UI）
deploy/            docker-compose · quick_start.sh
data/              db_raw(Chinook SQLite) · db_schema(生成 DDL/术语种子) · docs_raw / docs_parsed / eval_cases
docs/
  contracts/       Trace JSON Schema · eval 用例格式（W1 末与 B/C 冻结）
```

## 开发

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q                  # 27 passed（集成用例需先启动 DB）
.venv/bin/python scripts/smoke_nl2sql.py              # mock 驱动 10 用例全流程
.venv/bin/python eval/model_selection.py              # D1 选型评测（需 API key）
```

注意：受限网络环境下 `deploy/docker-compose.yml` 使用 `DOCKER_REGISTRY_MIRROR`（.env 已配 `docker.m.daocloud.io`）拉取镜像。

## 里程碑

W1 地基+选型+最小闭环 → W2 编排内核+缓存 → W3 中级任务主攻(#1/#2/#4/#5) → W4 跨源+澄清 → W5 打磨提交。
当前状态见 [docs/weekly/W1_progress.md](./docs/weekly/W1_progress.md)。
