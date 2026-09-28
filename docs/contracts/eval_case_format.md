# 契约三：Eval 用例格式 v0.2 ✅ FROZEN（2026-09-26 · C 定稿，A/B 提供输入）

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

## RAG 用例（v0.1 定稿，已入库 6 例）

```json
{
  "id": "rag-001",
  "category": "rag_single_doc",
  "question": "普通客户的工单必须在多长时间内首次响应？",
  "expected_facts": ["24"],
  "notes": "事实型-sop"
}
```

- **评分双指标**（定位失败层）：
  - `retrieval_hit`：expected_facts 全部出现在 top-k 检索文本中（纯检索质量，不耗 LLM）
  - `answer_hit`：expected_facts 全部出现在生成答案中（端到端，当前为忠实度代理）
- 匹配规则：**空白不敏感**（PDF 硬换行会拆开 token，如 "5 天"→"5\n天"）
- W2 升级：LLM-as-judge 双评（C 主导），expected_facts 保留作自动下限指标
- 用例文件：`backend/eval/cases/rag_single_doc.jsonl`；runner：`eval/run_rag.py`

## 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| 0.1 | 2026-09-21 | A 起草 NL2SQL 部分（10 用例已入库）|
| 0.1+ | 2026-09-22 | RAG 部分定稿（双指标：retrieval_hit/answer_hit，空白不敏感匹配）+ 语义 4 级匹配器说明 |
| **0.2** | **2026-09-26** | **冻结**：NL2SQL 31 用例 + RAG 8 用例入库，双 runner + preflight 门禁就绪；W3 C 扩量（鲁棒性×30/澄清×30/多轮×8/跨源×10）按本格式追加 | **✅ FROZEN（A/B/C 签字）** |
| 0.3 | 2026-09-30 | 追加多轮脚本用例格式（冻结后可选扩展，按规则登记）：4 个种子脚本 + runner，覆盖 §4.6 四模式 | 已登记 |
