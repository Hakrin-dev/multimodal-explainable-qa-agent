# 契约三：Eval 用例格式 v0.1（W1 末冻结 · 负责：C 定稿，A/B 提供输入）

> 用例文件：`backend/eval/cases/*.jsonl`（一行一用例）
> 跑分脚本：`backend/eval/run_nl2sql.py`（NL2SQL）；RAG runner 由 B/C 在 W2-W3 补齐

## NL2SQL 用例

```json
{
  "id": "st-001",                       // 稳定 id：{类别缩写}-{序号}
  "category": "nl2sql_single_table",    // nl2sql_single_table | nl2sql_multi_table | robust | ...
  "question": "数据库里一共有多少种音乐曲风？",
  "reference_sql": "SELECT COUNT(*) FROM genre",
  "notes": "count"                      // 覆盖点标签，供分组统计
}
```

- **评分**：执行准确率（execution accuracy）——候选 SQL 与 reference_sql 的结果集对比
  （有序比较；数值容差 1e-4；最多取前 200 行）
- reference_sql 必须含确定性 ORDER BY（无序语义时也要指定一个稳定序）
- 系统自修复循环成功 = 得分；另报 **免修复首过率**（W1 选型评测的裁决指标之一）

## RAG 用例（B/C W2 定稿，占位）

```json
{
  "id": "rag-001", "category": "rag_single_doc", "question": "年假有几天？",
  "expected_facts": ["15 天"],          // 关键事实自动匹配
  "judge": "llm_as_judge"               // 双评之一
}
```

## 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| 0.1 | 2026-09-21 | A 起草 NL2SQL 部分（10 用例已入库）|
