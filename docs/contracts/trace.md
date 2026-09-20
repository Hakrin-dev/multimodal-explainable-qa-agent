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
| `citations[]` | rag 节点 | `{doc, page, snippet}` |
| `slots` / `missing_slots[]` / `options{}` | clarify | 槽位状态 |
| `cost_rmb` / `model` / `tokens{}` | llm_call | 用量记账（与 SQLite 账本冗余，便于单轮成本归因） |

## 4. 传输与持久化

- API：`GET /api/trace/{turn_id}` 返回完整树 JSON（`to_dict()`）；SSE 过程中按节点推送 `trace.node` 事件（W2 定稿）
- 持久化：W2 落 PG 表（`trace_event`，一行一节点 = `flat_events()` 的形态），本周先内存 + JSONL 落盘

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
