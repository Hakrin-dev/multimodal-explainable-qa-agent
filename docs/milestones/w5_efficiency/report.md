# W5 效率与伸缩性实测报告（PLAN §7）

> 实测日期：2026-10-04 · 环境：WSL2 (Ubuntu 24.04) + RTX 5060 Laptop · 数据：
> `efficiency_20261004_182211.json` · 图表：`latency_vs_docs.png` / `table_scale_linking.png`
> 脚本：`backend/scripts/bench_efficiency.py`（文档规模用隔离 schema，互不污染）

## 1. 文档规模矩阵（检索链路：向量 + jieba-BM25 + RRF）

| 文档规模 | chunks | 检索 P50 | 检索 P95 | 标记事实召回@6 |
|---|---|---|---|---|
| 10 篇（真实语料） | 116 | **7.4 ms** | 9.1 ms | — |
| 100 篇（合成） | 300 | **7.2 ms** | 9.1 ms | **3/3** |
| 1000 篇（合成） | 3000 | **133.0 ms** | 150.4 ms | **3/3** |

**分析与瓶颈**：向量路为内存矩阵乘（numpy），1000 篇仍 <1ms 量级；
**瓶颈在内存版 jieba-BM25**（纯 Python 逐块打分，O(N_chunks × 查询词数)），
100→1000 篇时线性放大到 ~128ms。这正是 PLAN 预留的升级路径：
pgvector HNSW 索引 + 数据库全文/外部索引替换内存 BM25（决赛 Qdrant 分片）。

## 2. 表规模矩阵（Schema Linking：双路召回 + LLM 精排）

| 表规模 | 语义卡总字符 | Linking P50 | 目标表召回 |
|---|---|---|---|
| Chinook 11 表 | 2,099 | **55.2 ms** | **3/3** |
| 合成扩展 71 表 | 7,379 | **160.4 ms** | **3/3** |

**分析与瓶颈**：召回为关键词（O(N_tables)）+ 向量（71×512 点积，可忽略），
增长主要来自**逐表语义卡构造与候选打分**；精排只处理 top-k 候选，与总表数弱相关。
71 表下 P50 160ms、召回无损——Schema Linking 在初赛（11 表）与决赛
（AdventureWorks 71 表）规模均可用；进一步优化 = 语义卡缓存（当前进程内已缓存）
与召回索引化（pgvector 化表卡，W5+ 可选）。

## 3. 端到端与复杂度小结（供技术文档）

| 模块 | 复杂度 | 实测印证 |
|---|---|---|
| 混合检索 | 向量 O(N·d) / BM25 O(N·L) | 1000 篇 133ms（BM25 主导） |
| Schema Linking | 召回 O(N_tables)，精排 O(k·L_card) | 71 表 160ms |
| NL2SQL 端到端 | 主导项为 LLM 生成（P50 ~2s/轮） | L2 实测（见 W5 进度） |
| 缓存 | 前缀缓存命中 ~89%（generate 侧） | 成本账本（`/api/usage`） |

## 4. 复现

```bash
python scripts/gen_bench_corpus.py --count 100  --out data/docs_bench/100
python scripts/gen_bench_corpus.py --count 1000 --out data/docs_bench/1000
python scripts/bench_efficiency.py --repeats 5     # 隔离 schema + 出图
```
