# 多模态可解释问答智能体技术实现说明书（骨架）

> 版本：v0.1（W3 骨架，2026-10-03）  
> 目标篇幅：≤30 页。命令、字段和版本以仓库代码及契约为准。

## 1. 环境与启动

```bash
./deploy/quick_start.sh
cd backend && .venv/bin/python -m pytest tests -q
cd frontend && pnpm test && pnpm build
```

默认使用 mock LLM；真实 provider 通过 `.env` 配置，密钥不得提交。

## 2. 目录与模块索引

| 模块 | 入口 | 说明 |
|---|---|---|
| Agent | `backend/app/agent/` | 意图、计划、DAG、会话与澄清 |
| NL2SQL | `backend/app/nl2sql/` | 改写、链接、生成、执行、修复 |
| RAG/摄入 | `backend/app/rag/`, `backend/app/ingestion/` | PDF、质量/复杂度、向量检索、Citation |
| LLM | `backend/app/core/llm.py` | provider、响应缓存、用量账本 |
| API | `backend/app/main.py` | REST、SSE、Trace、PDF、usage |
| 前端 | `frontend/src/` | SSE 对话、数据卡片、引用、Trace/DAG |
| 评测 | `backend/eval/` | family runner、分级调度、报告 |

## 3. API 与事件契约

- REST：`/api/health`、`/api/chat`、`/api/nl2sql`、`/api/rag`、`/api/usage`。
- SSE：`turn.start → trace.node/clarify.request → answer.delta → answer.done → turn.end`。
- 完整字段：[`docs/contracts/trace.md`](../contracts/trace.md) 与 [`trace_schema.json`](../contracts/trace_schema.json)。
- 回放：`GET /api/trace/{turn_id}`；引用：`GET /api/docs/{doc_id}/pdf?page=N`。

## 4. 数据与持久化

### 4.1 PostgreSQL

说明 `kb_document`、`kb_chunk`、术语、Trace、会话和公式表；补充迁移版本与索引图（待填）。

### 4.2 SQLite 用量账本

`llm_usage` 记录 provider/model/purpose、prompt/completion tokens、缓存 tokens、费用、缓存命中和延迟；聚合逻辑集中在 `app/core/usage.py`。

## 5. 关键实现说明

### 5.1 首事件超时与手动停止

前端仅在 12 秒内没有任何 SSE 事件时中止；收到首事件后清除计时器，用户可点击“停止生成”触发 `AbortController`。

### 5.2 Schema Linking 与 DAG

Schema Linking 采用关键词/向量召回、LLM 精排、列级标注和 FK 路径注入；HYBRID 计划按拓扑波次执行，独立节点最多并发 3 个。

### 5.3 摄入与质量

复杂度评分选择 PyMuPDF/MinerU 路由；质量评估输出逐页报告、warnings 和可追踪 metadata；失败在写入 embedding 前终止。

## 6. 评测、复现与报告

```bash
cd backend
.venv/bin/python eval/run_levels.py --level L0
.venv/bin/python eval/run_levels.py --level L1
.venv/bin/python eval/usage_report.py --md var/eval/usage_report.md
```

每份报告应记录 commit、provider、model、数据库/模型版本、用例数量、准确率、延迟、tokens 和费用。

## 7. 故障排查

| 现象 | 检查 |
|---|---|
| 前端服务离线 | `/api/health`、Vite proxy、后端端口 |
| SSE 无首事件 | 后端日志、反向代理 buffering、12 秒首事件保护 |
| 查询连接失败 | PostgreSQL 健康状态、5433 转发、只读连接重试 |
| Citation 打不开 | `doc_id`、PDF 文件、页码和 `/api/docs` 列表 |

## 8. 发布检查清单

- [ ] 全量测试与前端构建通过
- [ ] L1 报告和成本报告归档
- [ ] Trace/Citation 字段未绕过契约变更
- [ ] 不提交 `.env`、API key、模型权重和运行产物
- [ ] 更新对应周报和版本变更记录
