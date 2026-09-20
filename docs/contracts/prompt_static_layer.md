# 契约二：Prompt 三层模板 · 静态层定稿候选 v0.1（W2 冻结 · 负责：A 起草）

> 关联决议：D10（W2 落地 Prompt 缓存前缀设计）、§11.3（三层缓存设计）
> 实现代码：`backend/app/core/prompts/`（nl2sql.py / agent.py）

## 1. 为什么分层

提供商前缀缓存对 **Prompt 前缀完全一致** 的请求按折扣计费（DeepSeek 命中价约为原价 1/10，
Qwen 隐式上下文缓存同理）。三层结构保证最大公共前缀稳定：

```
[静态层 A]  角色 + 通用规则 + 输出格式规范        ← 跨请求不变（冻结）
[半静态层 B] schema 语义卡 / 术语样例 / 工具清单   ← 同库/同文档集内稳定
[动态层 C]  用户问题 + 多轮历史 + 失败反馈        ← 每次变化，永远放最后
```

## 2. 冻结纪律（W2 起生效）

1. 静态层文本**只允许追加新规则（加在末尾），不允许改写/删除/换序**已有内容。
2. 任何静态层变更必须 bump `PROMPT_TEMPLATE_VERSION`（`app/core/llm.py`），
   使应用层响应缓存整体失效（这是刻意的：旧缓存对应旧模板）。
3. 半静态层内容按数据资产版本化（schema 卡更新 → bump 版本号，不拼在静态层里）。
4. `LLMService.chat()` 的调用方严禁在 system 消息里混入动态内容。

## 3. 静态层定稿候选（本文档即评审对象）

### 3.1 `nl2sql.generate`（已上线 v0.1，见 `prompts/nl2sql.py::STATIC_RULES`）

- 角色：PostgreSQL 数据分析助手，只读 SELECT
- 6 条规则：单条只读 SELECT / 仅用给定 schema / 仅用给定外键路径 / 聚合显式 GROUP BY
  + ORDER BY 语义 + TOP-N 必带 LIMIT N / 输出自检 / 缺要素时输出 SQL: None（澄清钩子）
- 输出格式：`【分析】…\n【SQL】\n\`\`\`sql … \`\`\``（双步输出：草稿进 Trace 展示）

### 3.2 `nl2sql.summarize`（已上线 v0.1，`prompts/nl2sql.py::SUMMARIZE_SYSTEM`）

- 角色：数据分析解说员；直接回答数字/实体，空结果如实说明，禁止编造

### 3.3 `agent.intent`（W2 候选，`prompts/agent.py::INTENT_STATIC`）

- 5 类意图（CHAT/DB_QUERY/DOC_QUERY/HYBRID/AMBIGUOUS）+ 判断依据 4 条
- 输出：严格 JSON（intent/confidence/slots/missing_slots/sub_tasks）
- AMBIGUOUS 兜底原则：拿不准宁可澄清，不要猜

### 3.4 待 B 起草

- `rag.generate`（引用规范：内联 [1][2] + 忠实度约束）
- `rag.query_rewrite`（多轮融合 + HyDE 可选开关）

## 4. 半静态层内容清单（随数据资产演进，W2-W3 逐步充实）

| 块 | 内容 | 版本化键 |
|---|---|---|
| schema 语义卡 | 表名 + 业务描述 + 列注释 + FK + top 示例值（`schema_meta.build_schema_context`） | DB 资产版本 |
| Join 路径 | 外键图搜索结果（W3 #5 注入） | 同上 |
| 术语样例 | 术语库高置信词条节选（W3） | 术语库版本 |
| 工具清单 | 编排内核注册的工具签名（W2） | 代码版本 |

## 5. 收益核算口径（效率评分项素材）

- `LLMService` 每次调用记录 `cached_prompt_tokens`（DeepSeek 返回 `prompt_cache_hit_tokens`）
- 账本表 `llm_usage` 已有列；`GET /api/usage` 聚合输出
- W2 起每日报表输出「命中率 / 未命中价×tokens / 命中价×tokens」对比曲线

## 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| 0.1 | 2026-09-22 | A 起草：nl2sql 两模板上线文本 + intent 候选 + 冻结纪律 |
