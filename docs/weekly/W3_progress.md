# W3 进度（10/5-10/11 计划周 · A 线 9/24 提前完成主攻任务）

> A 角色按 PLAN §13 W3 排期主攻：#4 Schema Linking 完整版（LLM 精排 + 列级）、#5 Join 路径（W3 前期已交付）、#1/#2 术语库在线匹配全链路、空结果归因。本档记录 A 线交付；B/C 顺延项见风险清单。

## 交付总览

| W3-A 任务 | 状态 | 关键结果 |
|---|---|---|
| #4 LLM 精排过滤干扰表 | ✅ | 平均表数 **7.5→2.1/11**，schema tokens **-74%**（v0 为 -30%）；L1 回归 **31/31=100%** |
| #4 列级 linking | ✅ | 列语义卡（kw+向量双路）→ 压缩 schema 内"相关列"标注；列召回（name级）64%（10/31 全中） |
| #5 Join 路径注入 | ✅（W3 前期） | FK 生成树显式路径入 prompt；精排删表后连通性自动桥接修复 |
| #1/#2 术语库在线匹配全链路 | ✅ | 三级匹配：精确别名 → 编辑距离（错别字，jieba 词典词守卫）→ pgvector 向量（语义别名，sim≥0.75）；129 术语全量向量化（列维 1024→512 对齐本地 bge-small） |
| #1/#2 LLM 批量别名生成 | ✅ | **+629 别名**入库（source='llm'），129/129 术语有别名；4 个泛化风险别名人工审查剔除（经典/音乐/电影等） |
| 空结果归因（§4.2 ⑥-5） | ✅ | sqlglot 提取 WHERE 过滤值 → distinct 值核对：`suspicious_filter`（附最近似值，触发定向修复）/ `truly_no_data`（下推 count(*) 佐证）/ `unknown`；零 LLM 成本 |
| 语义空判定修复 | ✅ | COUNT(*) 空集仍返回一行（值为0）——此前 status 误判 ok；补 `_is_semantically_empty`（单行单列 0/NULL） |

## 消融数据（W3 核心交付物）

| 指标 | 全 schema（基线） | Linking v0（W2） | **Linking v1（W3：+LLM精排+列级）** |
|---|---|---|---|
| L1 准确率（31 用例） | 100% | 100% | **100%（首过 97%）** |
| Prompt 平均表数 | 11 | 7.5 | **2.1** |
| Schema tokens | 1280 | ~896 (-30%) | **~331 (-74%)** |
| 精排触发/平均删表 | — | — | 31/31 次，均删 5.4 表 |
| 列召回（name 级） | — | — | 64%（10/31 全中，离线零成本可复算） |

> 规模外推：v1 的压缩收益在 AdventureWorks（71 表）规模会从 -74% 进一步放大（精排删表比例随候选池增大而上升）。

## 过程中的重要决策与负结果

1. **Rule-7 实验回退（负结果，已登记契约 1.2）**：精排上线后发现 mt-021 在 2 表压缩上下文下生成 `COUNT(DISTINCT t.name)`（1213）与参考 `COUNT(*)`（1297）不符。曾试验追加静态层规则 7/8 修正——**与 track 表业务注释（W1 name-dedup 约定）直接冲突**，导致 st-004/mt-008 回归失败。已完整回退静态层（v1.0 文本零改动、版本维持 v0.4-w2-d3），改为**修正 eval 数据 bug**：mt-021 参考SQL 对齐 name-dedup 约定（与 st-004/mt-008 参考一致）。教训：**静态层规则不得与半静态层业务注释冲突；参考 SQL 语义要先对齐表注释约定**。
2. **count-distinct 启发式回退**：仿 topn-limit 的"计数问题 + COUNT(DISTINCT 非主键) → 修复"护栏因上述约定冲突一并移除（其别名解析逻辑 sqlglot 化的代码保留在 git 历史）。
3. **别名治理**：LLM 生成的 629 别名先落 `data/db_schema/alias_candidates.jsonl` 人审再入库（`gen_aliases.py --apply`）；泛化词（经典/音乐/电影/歌曲）会误伤子串改写，已剔除 4 个。
4. **环境问题记录**：WSL2 + docker-proxy 在持续混合负载下间歇性断 5433 端口转发（dmesg: `CheckConnection: getaddrinfo() failed`；空闲时 90/90 探测全通）。`eval/ablate_w3.py` 已加每用例退避重试（4 次 × 15s）作为评测基建加固；quick_start 面向常规 Linux/docker 环境不受影响。

## 回归验证（W3 收口态）

| 项 | 结果 |
|---|---|
| pytest | **91/91**（77 → +14：精排 7 / 空归因 7） |
| NL2SQL L1（31 用例，别名扩充后） | **31/31 = 100%**（首过 97%，mt-018 修复 1 轮） |
| 多轮（4 脚本 14 轮） | **14/14** |
| RAG（检索 hit@6 / 答案命中） | **100% / 100%** |
| 改写冒烟 | 摇磙→exact命中 / 蓝调→Blues / 巴萨诺瓦→Bossa Nova |

## 新增/变更文件

- `app/nl2sql/schema_linking.py`：+`rerank()`（LLM 精排+连通性修复+优雅降级）、+`column_link()`（列级索引/标注）、`build_compressed_context(relevant_cols)`
- `app/nl2sql/pipeline.py`：精排接入（link_rerank span）、空结果归因（empty_attr span + 定向修复）、语义空判定
- `app/nl2sql/empty_attr.py`：新模块（sqlglot 过滤值提取 + distinct 核对 + difflib 最近值）
- `app/nl2sql/rewriter.py`：v1 三级匹配（exact→edit→vector），纯模式契约不变
- `app/core/prompts/nl2sql.py`：+`LINK_RERANK_STATIC` 家族 #8（其余冻结文本零改动）
- `scripts/embed_terms.py`（向量回填+维度对齐+HNSW）、`scripts/gen_aliases.py`（LLM 别名生成/审查/合并）
- `eval/ablate_w3.py`（消融 runner：准确率+表数/token/精排明细一屏采集 + 基建重试）
- `tests/test_w3_linking.py`（+14 测试）；`eval/cases/nl2sql_multi_table.jsonl`（mt-021 参考修正）
- `data/db_schema/alias_candidates.jsonl`（人审产物，+629 别名已入库）

## 风险清单（B/C 催办，自 W2 顺延）

| 项 | 顺延周 | 状态 | 影响 |
|---|---|---|---|
| B：摄入流水线 v1（质量检测器/复杂度评分/MinerU/坏文档） | W2→W3 | ⚠ 仍未提交 | #9/#7/#8 全部阻塞；W4 跨源与公式依赖摄入产物，**周五前必须开工** |
| C：前端联调收尾（12s 超时修复）+ DAG 可视化 + 文档管理台 | W2→W3 | ✅ 联调/DAG 已收尾；管理台顺延 W4 | W4 演示 8/10 场景已具备；文档管理台仍依赖 B 的上传/质量报告/修复 API |
| C：分级跑分脚本 L0/L1（eval 契约 C 主责部分） | W3 | ✅ | `eval/run_levels.py` 统一调度 NL2SQL/RAG/多轮；L0 每类 5 用例、L1 全量、L2 主备模型×重复；结果落 `var/eval/levels_*.json` |
| C：技术文档骨架（设计报告 + 技术实现说明书） | W3 | ✅ | `docs/design/design_report.md`、`technical_implementation.md` 已建立，纳入架构、契约、评测、成本与验收清单 |

> 建议周五复盘会（本周按日历已是 W1 尾/提前量充足）逐项确认 B/C 排期；A 侧 W3 交付已 100% 完成，W4 可提前介入跨源多跳与澄清基础版。


## A 复审（2026-09-24，审阅 workbuddy 的 W3 提交 bae827e）

**结论：合入。** 消融声明全部复现（91/91 测试、L1 31/31、2.1 表/-74% tokens/精排 31 次触发），
治理纪律良好（负结果完整回退并记录、新家族按协议注册、mt-021 与 W1 name-dedup 约定对齐）。

复审修正三处：
1. **W1 别名测试断言过度具体**（LLM 别名"摇滚曲风"最长匹配抢占"摇滚"）→ 改为别名集无关断言
2. **别名库治理**：+629 别名存在实质风险——泛化常用词作精确别名会误改写（"大多客户"→
   "Toronto 客户"、"安卓"→员工名、"身毒"过时译名、波城跨城歧义）。执行剪枝：
   黑名单 40 词 + 跨 canonical 歧义按**基础词规则**消解（Rock ⊂ Rock And Roll → 别名归 Rock），
   剩余歧义 0；规则已固化进 gen_aliases.py（review_prune）保证再生成安全
3. 环境：Windows 会话残留已清理（.workbuddy/ 移除 + gitignore；Windows 侧日志删除）


## A 复审 B/C（2026-09-30，审阅 af178df 及此前 B/C 提交）

**结论：合入。** B 摄入流水线 v1 与 C 前端超时+分级评测三线声明全部本机复现；复审修正一处落地。

### B（Songxy0919）摄入流水线 v1 — ✅ 合入

提交链：`8da6c5c`(复杂度评分 #8+解析路由) → `6c7a976`(质量评估 #9 rules-v1) → `2917c94`(三类坏文档资产) → `d253ceb`(MinerU 4.x 扫描件闭环) → `bfa6261`(embedding device/batch 配置化，W3 BGE-M3 切换基建)。对应 PLAN §13 W2→W3 顺延的 B 主交付，#9/#7/#8 阻塞解除。

本机复现（A 侧 WSL，mqa-db 容器）：

| 项 | B 声明 | A 复现 |
|---|---|---|
| pytest | 141 (MinerU 后) | **146/146**（+5 test_embedding_config，一致） |
| 摄入幂等 | 4 docs / 0 new chunks | ✅ 一致（content hash 判重） |
| meta quality+complexity | score 0.99+ / cx 1 | ✅ 0.9912~0.9953 / 1 / has_text_layer=true / warnings=[] |
| RAG | 8/8 retrieval hit@6 | ✅ 8/8 retrieval hit@6=100% / answer hit=100%（真 DeepSeek） |
| 坏文档资产 | 3 类 fixtures | ✅ test_bad_docs 5 passed |

代码审查：`mineru.py` 契约保真度高（docvortex.middle v2 schema 严格校验、subprocess 隔离+tempdir 清理+600s timeout env 可配、bbox 归一化→点+旋转反归一化、header/footer 过滤不入检索）；`quality.py` 5 维信号+结构化 warnings+1-based 页码；边界诚实（"不等同 OCR 字符准确率""不承担全文简繁转换"）。

**复审修正一处**：`mineru._resolve_binary` 错误消息硬编码 B 机器路径 `/home/sxy/.venvs/mqa-mineru/...` → 改为通用提示（避免误导其他开发者；测试无断言该路径，55/55 绿）。

遗留建议（非阻塞，记入 C 后续）：

- `run_rag.py` 尾注 `(with mock LLM, answer hit is expected 0%)` 为固定文案，实际 provider 已是 deepseek 仍显示 mock 误导——建议按 `s.llm_provider` 动态化（A 尝试改时因 Git Bash→wsl.exe 双引号传参吃反斜杠导致 f-string `\n` 转义破坏，已回退，留 C 修）

### C（LYR-t）前端超时 + 分级评测 — ✅ 合入

- `20ef563`：前端 SSE 总超时改**首事件超时**（12s 内收不到任何事件才中止，收到后 `clearTimeout`，HYBRID/复杂 DB_QUERY 允许跑完；用户 `AbortSignal` 仍可手动停）。慢轮次回归测试（60s 流不触发 abort）覆盖。逻辑正确；`catch(error){throw error}` 是无操作冗余，可后续清理。
- `af178df`：`eval/run_levels.py` 统一调度 L0/L1/L2，family runner 加 `--only-ids`，manifest 落 `var/eval/levels_*.json`。本机 L0 实测 **3 runs 0 failures**（multiturn L0 跑 mts-001~004 共 14 轮全过，真 DeepSeek）。

遗留建议（非阻塞）：

- `run_levels.py` L0 nl2sql family `--only-ids` 取合并列表前 5（全为 single-table），L0 smoke 永不覆盖 multi-table——建议 single 取 3 + multi 取 2
- `run_rag.py`/`run_multiturn.py` 的 `--only-ids` 过滤 `json.loads(l)` 每行调两次，轻微冗余

> B/C 三线顺延项至此全部收口；A 侧 W3 100% + W4-D1 开工。

## B 增量（2026-09-30）：BGE-M3 GPU 与 RAG Cross-Encoder 精排

### 本次完成

- 在独立 GPU 环境部署 BGE-M3，输出 1024 维归一化向量；
- EmbeddingService 新增可配置 device 和 batch size，CPU 默认行为保持不变；
- 使用独立数据库 `chinook_bge_m3_sxy` 完成 1024 维知识库重建，
  共 5 docs、39 chunks、39/39 embeddings，未修改原 512 维主库；
- 部署 `bge-reranker-v2-m3`，实现 RRF Top-20 → Cross-Encoder → Top-K；
- 新增 reranker 开关、模型路径、设备、batch、候选数和最大长度配置；
- reranker 模型按需加载；失败时记录告警并回退原 RRF 排名；
- Citation 六字段保持不变，`score` 保留 RRF 语义；
  原始 Cross-Encoder logit 仅写入调试字段 `rerank_score`；
- 新增 `eval/ablate_rag_rerank.py`，在相同候选与 embedding 下比较
  Hit@1、Hit@6、MRR、Top-1 变化和稳态延迟。

### GPU 验证

| 指标 | 结果 |
|---|---:|
| GPU | NVIDIA GeForce RTX 5090 |
| BGE-M3 向量维度 | 1024 |
| BGE-M3 峰值显存 | 约 2.20 GiB |
| BGE-M3 吞吐 | 约 1318 texts/s（256 条短文本） |
| BGE-M3 + reranker 同驻显存 | 约 4.37 GiB |
| 独立 Top-20 rerank P50/P95 | 12.36 / 12.39 ms |

### RAG 消融结果

测试集为现有 8 条单文档 RAG 用例，数据库为 5 docs / 39 chunks。

| 指标 | RRF | RRF + reranker |
|---|---:|---:|
| Hit@1 | 100% | 100% |
| Hit@6 | 100% | 100% |
| MRR | 1.000 | 1.000 |
| 稳态 P50 | 11.78 ms | 53.98 ms |
| 稳态 P95 | 12.96 ms | 58.22 ms |
| Top-1 发生变化 | — | 0/8 |

当前用例在 BGE-M3 + RRF 下已经全部 Top-1，因而本轮不能宣称 reranker
提升准确率；可以确认精排未降低当前召回，并以约 42 ms 稳态增量完成可配置接入。
后续需增加近义干扰、跨文档冲突和长文档候选集，才能评价精排的真实增益。

### 回归与边界

- reranker 专项及 RAG 测试：12 passed；
- CPU 主库全量回归：152 passed，5 warnings，0 failure；
- 模型目录由 `.gitignore` 排除，不进入版本库；
- 默认 `RERANK_ENABLED=0`，CPU 和现有容器部署行为不变；
- GPU 部署通过环境变量显式开启；
- 当前仅实现本地 reranker，SiliconFlow rerank API 备胎和困难评测集待后续；
- 当前完成 W3 B 的 GPU embedding 与精排增量，不代表忠实度自检和
  10 份知识库扩充已经完成。
