# 契约一：Trace 数据模型 v0.2 ✅ FROZEN（2026-09-26 · A 起草，B/C 评审，三方冻结）

> 权威定义：[`trace_schema.json`](./trace_schema.json)（JSON Schema）
> 运行时实现：`backend/app/core/tracing.py`（`TraceCollector` / `TraceNode`）

## 1. 定位

每轮用户提问产出一棵 Trace 树，是**可解释性评分（5 分）与前端 DAG 可视化的唯一数据源**。
后端所有模块（编排、NL2SQL、RAG、公式、融合、澄清）只往树里挂节点，不自行拼展示格式。

## 2. 树形结构

```
turn (root: question)
├── intent        # 意图分类 CHAT|DB_QUERY|DOC_QUERY|HYBRID|AMBIGUOUS
├── plan          # HYBRID 时的子任务 DAG（单意图场景可省略）
├── step/tool_call/llm_call ...   # 流水线内部节点
│   └── llm_call  # LLM 调用可嵌套在任意步骤下
├── clarify       # 澄清挂起（缺失槽位、候选选项）
└── fuse          # 跨源融合生成
```

## 3. 字段约定

| 字段 | 说明 |
|---|---|
| `type` | `turn`(根) / `intent` / `plan` / `tool_call` / `llm_call` / `fuse` / `clarify` / `step`(通用流水线步骤，label 区分：`rewrite`、`schema_link`、`join_path`、`sql_gen`、`sql_validate`、`sql_repair`、`sql_execute`、`summarize`…) |
| `label` | 人类可读名称，前端时间线直接展示 |
| `status` | `pending` / `ok` / `error` / `degraded`(降级成功) / `skipped` |
| `input`/`output` | 任意 JSON。体积控制：rows 最多截断 20 行、chunks 最多 3 条全文 |
| `detail` | 结构化扩展位，常用 key 约定见下表 |

**detail 常用 key（前端依赖，改动需三方确认）**：

| key | 出现节点 | 含义 |
|---|---|---|
| `intent` / `confidence` | intent | 分类结果与置信度 |
| `rewrites[]` | rewrite | `{before, after, reason}` 改写对照（可解释素材） |
| `sql` / `valid` / `errors[]` | sql_validate, sql_gen | SQL 与校验结果 |
| `repair_round` | sql_repair | 自修复第几轮（≤3） |
| `row_count` / `truncated` | sql_execute | 行数与是否截断 |
| `chart_hint` | summarize | 图表建议（bar/line/pie/null） |
| `citations[]` | rag_search | `{doc, doc_id, page, breadcrumb, snippet, score}` ——已定稿，实现见 `rag/retriever.py::ChunkHit.citation()`，前端可直接渲染 |
| `slots` / `missing_slots[]` / `options{}` | clarify | 槽位状态 |
| `cost_rmb` / `model` / `tokens{}` | llm_call | 用量记账（与 SQLite 账本冗余，便于单轮成本归因） |
| `original_query` / `retrieval_query` / `changed` / `attempted` / `hyde_enabled` / `degraded` / `reason` | rag_query_rewrite | 可选检索改写 step；失败为 degraded 并回退原查询；与 agent rewrite 节点独立 |
| `hyde_length` / `hyde_preview` | rag_query_rewrite | 假设文档字符数与最多 160 字符摘要，不记录全文，不进入 citations |

W5 B-D4：只有启用 `RAG_QUERY_REWRITE_ENABLED` 才新增 `rag_query_rewrite`
step，位置为检索之前，Agent 调用时嵌套于工具节点。成功返回的模型响应
（包括 JSON 校验失败的响应）挂 `llm_call` 子节点，purpose 为
`rag.query_rewrite`，记录 model、cost_rmb、tokens 和调用延迟；调用抛异常时
保留 step 的降级原因，不伪造调用用量。关闭时无新增节点或模型调用，
`RAGResult.query_rewrite` 及 `/api/rag` 可选响应字段仍记录 attempted=false、
reason=disabled。HyDE 仅影响 Dense embedding，BM25/reranker 使用
retrieval_query；生成和 factcheck 使用 Pipeline 输入问题与真实 chunk。

### 3.1 RAG Citation 字段类型

`citations[]` 的字段集合固定如下：

| 字段 | 类型 | 约定 |
|---|---|---|
| `doc` | string | 文档的人类可读名称 |
| `doc_id` | string | 稳定文档 ID |
| `page` | integer | 1-based，当前取命中 chunk 的 `page_start` |
| `breadcrumb` | string | 由 IR 的 `list[str]` 使用 `" > "` 拼接 |
| `snippet` | string | 命中 chunk 文本前 120 个字符 |
| `score` | number | RRF 融合分数，输出时保留 4 位小数 |

v0.1 Citation 支持 PDF 页级定位，不承诺页内矩形框选。当前 `kb_chunk`
没有持久化 `block_ids` 或 `bbox`；如后续增加精确高亮，需要同步修改
Ingestion IR、数据库迁移、Citation 契约、测试和 C 端渲染，不能单方添加字段。

## 4. 传输与持久化

### 4.1 HTTP 拉取

- `GET /api/trace/{turn_id}` 返回完整树 JSON（`to_dict()`）

### 4.2 SSE 过程推送（W2 `POST /api/chat/stream`，前端实时渲染）

后端在流式生成过程中按发生顺序推送以下事件（`event:` / `data:` 两行格式）：

| event | data | 时机 |
|---|---|---|
| `turn.start` | `{turn_id, question, session_id, resumed_clarify}` | 开始处理本轮请求；`turn_id` 用于断线后的 Trace 回放 |
| `trace.node` | `TraceNode`（flat 形态，含 `id`/`parent_id`） | 每个节点完成（含 status/error） |
| `answer.delta` | `{"text": "增量文本"}` | 最终答案流式生成中 |
| `answer.done` | 完整 `TurnResult`：`{question, answer, intent, status, data, citations, clarify, cost_rmb, latency_ms, trace}` | 答案完成；这是前端最终渲染锚点 |
| `clarify.request` | `{question, missing_slots[], options{}}` | 需要澄清，前端展示选项按钮；`options` 允许为空 |
| `turn.end` | `{latency_ms, cost_rmb, status}` | 本轮结束 |
| `error` | `{message, code, recoverable, node_id?}` | 错误事件；`code` 稳定、`recoverable` 表示是否可重试 |

约束：
- `trace.node` 的 `parent_id` 允许引用本轮根节点；根节点本身不一定以事件推送，前端需在 `answer.done.trace` 到达后补齐完整树；
- 事件顺序固定为 `turn.start` → `trace.node`/`clarify.request` → `answer.delta` → `answer.done` → `turn.end`；
- 当前实现由 worker 线程实时转发事件；CHAT/fuse 的 `answer.delta` 可按增量文本推送，`answer.done` 仍是最终一致性锚点；
- 断线恢复依赖 4.1：前端先用 `turn_id` 调用 `GET /api/trace/{turn_id}` 回放完整树；找不到的 turn 返回 404，处理中的 turn 由调用方重试；
- 所有事件 data 均为单行 JSON（换行转义）。

### 4.2.1 C 端评审结论（2026-09-23）

7 类事件足以覆盖对话、结构化结果、引用、澄清和错误态；`TraceNode` 的 `type + label + parent_id + status + latency_ms + detail` 足以完成时间线与 X6 DAG，不需要新增层级字段，层级可由父子关系计算。

冻结验收：`turn.start.turn_id`、`GET /api/trace/{turn_id}`、稳定的 `error.code/recoverable` 均已实现并由后端测试守护；`trace.node` 保持 `type` 与 `label` 职责分离，前端图标按 `type`、文案按 `label` 渲染。

### 4.3 持久化

- W2 落 PG 表（`trace_event`，一行一节点 = `flat_events()` 的形态），本周先内存 + JSONL 落盘 `var/traces/`

## 5. 示例

```json
{
  "trace_version": "0.1",
  "turn_id": "ab12cd34ef56",
  "question": "销量前10的曲目",
  "root": {
    "id": "ab12cd34ef56", "parent_id": null, "type": "turn", "label": "turn",
    "input": {"question": "销量前10的曲目"}, "output": null,
    "latency_ms": 4200, "status": "ok", "detail": {},
    "children": [
      { "id": "…-n1", "type": "intent", "label": "intent", "status": "ok",
        "output": {"intent": "DB_QUERY", "confidence": 0.98} },
      { "id": "…-n2", "type": "step", "label": "sql_gen", "status": "ok",
        "detail": {"sql": "SELECT t.Name, SUM(il.Quantity) …"} },
      { "id": "…-n3", "type": "step", "label": "sql_execute", "status": "ok",
        "detail": {"row_count": 10, "truncated": false} }
    ]
  }
}
```

## 6. 变更记录

| 版本 | 日期 | 变更 | 状态 |
|---|---|---|---|
| 0.1 | 2026-09-21 | A 起草初稿 | 已评审 |
| 0.1+ | 2026-09-22 | 补充 §4.2 SSE 事件格式初稿（7 类事件，供 C 前端 W2 开发） | 已评审 |
| 0.1+B | 2026-09-22 | B（sxy）完成 RAG Citation 评审；六字段实现与测试一致，明确字段类型和页级定位边界 | 已评审 |
| 0.1+C | 2026-09-23 | C 按运行时代码完成前端契约评审；修正实际 payload，提出 `turn_id` 与 Trace 拉取端点两项冻结条件 | 已评审 |
| **0.2** | **2026-09-26** | **冻结**：两项阻塞项已闭环（`turn.start` 携带 `turn_id` 已实现并有测试守护；`GET /api/trace/{turn_id}` 已实现含 404 语义）；事件顺序按实测更新；嵌套 Trace 树（tool_call 下挂流水线子树）为真实行为 | **✅ FROZEN（A/B/C 三方签字）** |
| 0.2+B-D4 | 2026-10-03 | 新增可选 rag_query_rewrite step/detail 和模型用量子节点；六字段 Citation 与现有字段语义不变 | 已登记 |

> 冻结后变更规则：字段改名/删除须三方向意；新增可选字段由提出方在变更记录登记即可。
> C 的非阻塞建议（error.code/recoverable、类型图标映射）进入 W2 待办，不阻塞本版。
