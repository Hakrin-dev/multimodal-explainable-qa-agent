# W1 里程碑验证证据（2026-09-26 收官）

> W1 产出物三件套：选型报告、最小闭环 demo 证据、三份契约文档。
> 本目录归档 demo 实拍截图；指标证据见各回归报告与进度文档。

## 截图清单（playwright 无头浏览器实拍，前端经 nginx 容器 :8090 真实渲染）

| 文件 | 场景 | 对应演示脚本（PLAN §9） |
|---|---|---|
| `01_home.png` | 前端首页（C 的 Vue 3 + Naive UI 骨架，产品名「问迹」） | — |
| `02_db_query.png` | 单表问数：对话流 + SQL/表格数据卡片 + Trace 时间线 | 场景 1（销量前十曲目）|
| `03_clarify.png` | 主动澄清：缺槽位挂起 + 候选选项展示 | 场景 4（增长率）|
| `04_clarify_resume.png` | 澄清回填续查：补时间范围后自动续算 | 场景 4 续 |
| `05_rag_citation.png` | 单文档问答：答案 + 引用列表（文档/页码/面包屑） | 场景 2（年假）|

## 指标证据（同一时期实测）

- **NL2SQL L1 全量：31/31 = 100%**（`backend/var/eval/nl2sql_20260922_185416.json`）
- **RAG L1：8/8 = 100%**（retrieval + answer 双指标，`rag_20260922_185926.json`）
- **LLM 选型终测**：DeepSeek-V3 双维度胜出（`model_selection_20260922_184848.md`）
- 检索 recall@6 冒烟 8/8（全部 rank#1）；SSE 真流式实测 45 增量/轮
- 后端测试 63 通过；成本账本 W1 总花费 ≈ ¥3（预算 ¥10 内）

## 复现命令

```bash
./deploy/quick_start.sh                          # 一键全栈 + 冒烟
cd backend && .venv/bin/python eval/run_nl2sql.py --cases eval/cases/nl2sql_single_table.jsonl eval/cases/nl2sql_multi_table.jsonl
cd backend && .venv/bin/python eval/run_rag.py
# 截图：playwright 1.62（浏览器缓存 chromium-1234），脚本思路见本文件头部场景表
```
