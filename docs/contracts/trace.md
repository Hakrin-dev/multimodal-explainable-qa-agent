# 契约一：Trace 数据模型 v0.1（W1 末冻结 · 负责：A 起草，B/C 评审）

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
| `turn.start` | `{question, session_id, resumed_clarify}` | 开始处理本轮请求 |
| `trace.node` | `TraceNode`（flat 形态，含 `id`/`parent_id`） | 每个节点完成（含 status/error） |
| `answer.delta` | `{"text": "增量文本"}` | 最终答案流式生成中 |
| `answer.done` | 完整 `TurnResult`：`{question, answer, intent, status, data, citations, clarify, cost_rmb, latency_ms, trace}` | 答案完成；这是前端最终渲染锚点 |
| `clarify.request` | `{question, missing_slots[], options{}}` | 需要澄清，前端展示选项按钮；`options` 允许为空 |
| `turn.end` | `{latency_ms, cost_rmb, status}` | 本轮结束 |
| `error` | `{message, node_id?}` | 不可恢复错误 |

约束：
- `trace.node` 的 `parent_id` 允许引用本轮根节点；根节点本身不一定以事件推送，前端需在 `answer.done.trace` 到达后补齐完整树；
- 事件顺序固定为 `turn.start` → `trace.node`/`clarify.request` → `answer.delta` → `answer.done` → `turn.end`；
- 当前实现将完整处理结束后缓存的事件一次性写入响应，`answer.delta` 也是整段答案；逐 token 真流式属于 W2 联调项；
- 断线恢复依赖 4.1，但 `turn.start` 尚不携带 `turn_id`、4.1 端点也尚未实现，当前前端只能提示重试；冻结 v0.2 前需由 A 补齐；
- 所有事件 data 均为单行 JSON（换行转义）。

### 4.2.1 C 端评审结论（2026-09-23）

7 类事件足以覆盖对话、结构化结果、引用、澄清和错误态；`TraceNode` 的 `type + label + parent_id + status + latency_ms + detail` 足以完成时间线与 X6 DAG，不需要新增层级字段，层级可由父子关系计算。

冻结前需完成两项阻塞修订：

1. `turn.start` 必须增加 `turn_id`，使断线发生在 `answer.done` 前时仍能恢复；建议同时保留 `session_id` 与 `resumed_clarify`。
2. 实现 `GET /api/trace/{turn_id}`，并明确找不到、处理中、已完成三种状态码/响应。前端在此之前不承诺断线自动恢复。

非阻塞建议：`error` 增加稳定的 `code` 和 `recoverable`；`trace.node` 保持当前节点类型与 label 的职责分离，图标按 `type`、文案按 `label` 渲染。

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
| 0.1 | 2026-09-21 | A 起草初稿 | 待 B/C 评审 → 周五冻结 |
| 0.1+ | 2026-09-22 | 补充 §4.2 SSE 事件格式初稿（7 类事件，供 C 前端 W2 开发） | 待评审 |
| 0.1+B | 2026-09-22 | B（sxy）完成 RAG Citation 评审；六字段实现与测试一致，明确字段类型和页级定位边界 | B 已评审；整体仍待 A 处理两个阻塞项 |
| 0.1+C | 2026-09-23 | C 按运行时代码完成前端契约评审；修正实际 payload，提出 `turn_id` 与 Trace 拉取端点两项冻结条件 | C 已评审，待 A 处理阻塞项 |
| 0.2 | （周五） | B/C 评审意见合并后冻结 | 待定 |
