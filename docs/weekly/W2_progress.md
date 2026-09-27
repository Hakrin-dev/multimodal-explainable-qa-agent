# W2 进度日志（9/28-10/4）· A 角色周报

> W1 总结见 `W1_progress.md` 末尾。W2 A 的排期任务（PLAN §13）：
> 编排内核 + LLM 抽象层（W1 已提前交付）→ 本周主交付：**Prompt 静态层冻结（D10）+
> 缓存命中率统计 + 内核 W2 增量**。

## D1（9/28）· A 完成 ✅

### 交付物

| 项 | 状态 | 说明 |
|---|---|---|
| **Prompt 静态层冻结 v1.0**（D10 W2 交付） | ✅ | 六家族注册表（nl2sql×2 / agent×3 / rag×1）定稿；**文本零改动冻结**（保住双百基线与全部缓存）；新家族注册协议入档（B 的 rag.query_rewrite / rag.factcheck 待注册）；版本号规则明确（文本变更才 bump） |
| **缓存命中率首次统计**（W2 产出） | ✅ | `app/core/usage.py`（权威逻辑）+ `eval/usage_report.py`（CLI）+ `GET /api/usage` 增强 `stats` 块（C 看板直连）；账本改进：缓存命中也记 tokens（可算节省额） |
| error.code/recoverable | ✅ | C 评审非阻塞建议落地（SSE error 事件携带稳定 code 与可恢复标记） |

### 首份缓存经济报告（全量账本，2026-09-22 快照）

- 真实调用 852 次 + 应用层缓存命中 94 次；prompt tokens 1,068,080
- **前缀缓存命中 595,317 tokens，命中率 55.7%**（nl2sql.generate 侧 61%）
- 实际成本 **¥0.96** vs 无前缀缓存 ¥2.03 → **节省 ¥1.07（53% 成本削减）**
- 报告归档：`backend/var/eval/usage_cache_report_w2d1.md`；每日成本曲线数据就绪
- 顺手修正：选型报告的"≈¥3"成本说法改为账本实数 ¥0.96

### 测试

68 通过（+3 usage 统计）；`/api/usage` stats 块容器内实测可用。

## D2（9/29）· A 完成 ✅ —— 编排内核 v1.1（多轮 + 确定性澄清）

| 项 | 状态 | 说明 |
|---|---|---|
| **指代消解改写**（§4.6，A 主责） | ✅ | `agent/rewrite.py`：追问→自包含问题（实测"那 2023 年呢"→"2023 年销售额是多少"→正确作答）；改写对照进 Trace + `TurnResult.rewritten`（UI 展示素材）；无历史/寒暄跳过、解析失败回退原问题 |
| **槽位矩阵外置**（§4.7） | ✅ | `data/slot_matrix.json`（增长率/对比/趋势三规则 + `satisfied_by` 证据正则）+ `agent/slots.py`：LLM 过度自信时确定性覆写为澄清、AMBIGUOUS 时选项补强；新领域改 JSON 零代码 |
| **澄清超时清理** | ✅ | 挂起态带时间戳，TTL 600s，过期不再遮蔽新问题 |
| agent.rewrite 家族注册 | ✅ | Prompt 契约第七家族（含与 B 未来 rag.query_rewrite 的职责边界注记） |

实测：多轮改写 ✓ / 矩阵选项增强 ✓ / 回填续查（改写器合成自包含问题）✓；
测试 72 通过（+4：改写器/矩阵规则/覆写/TTL）。
已知边界：空结果归因（"客户增长率"类口径问题）为 W3 项，澄清选项已含口径候选。

## D3（9/30）· A 完成 ✅ —— HYBRID 真依赖 DAG（内核 v1.2）

| 项 | 状态 | 说明 |
|---|---|---|
| **任务图规划** | ✅ | planner 升级：{id, tool, question, depends_on} schema + edges 入 Trace；legacy 线性计划自动归一化为依赖链 |
| **并行波次执行** | ✅ | 拓扑排序 + ThreadPoolExecutor(×3)：独立子任务并发（Barrier 会合点测试**证明并行性**）；环依赖容错回退串行 |
| **紧凑实体注入** | ✅ | {tN.result} 替换用首行字符串单元格（实体名）而非完整摘要——修复长摘要稀释检索（实测 Margaret 方法论 chunk 从 rank7+ 回到 rank2） |
| agent.plan 演进登记 | ✅ | 冻结家族的 schema 变更走登记例外（契约变更记录 + PROMPT_TEMPLATE_VERSION v0.4-w2-d3） |
| 顺手修 bug | ✅ | DAG 重写丢 parent 传递（子任务流水线平铺）——嵌套断言测试守住 |

实测：依赖链（冠军→方法论，双源分区溯源）✓；并行（摇滚vs爵士销量 + 提成规定，**4.6s** 双源并发）✓。
测试 76 通过（+4：并行证明/依赖替换/环容错/legacy 兼容）。
已知小瑕疵：fuse 对已命中的方法论内容措辞偏保守（"未找到可直接对应"但随即引用了内容）——冻结模板不动，记 W3 迭代。

## D4（9/30）· A 完成 ✅ —— 多轮验收 + 演示覆盖度矩阵

| 项 | 状态 | 说明 |
|---|---|---|
| **多轮脚本 runner + 4 种子场景** | ✅ | `eval/run_multiturn.py`（逐轮断言：status/intent/facts/rewritten_contains，会话隔离）；mts-001~004 覆盖 §4.6 四模式（指代追问/话题切换/澄清恢复/混合 6 轮长对话）——**4/4 脚本 14/14 轮全过**（改写器连文档上下文都带入："那事假呢"→"按员工手册，事假怎么算？"） |
| **演示场景覆盖度矩阵** | ✅ | `scripts/demo_scenarios.py`：PLAN §9 十场景自动检查——**可演示 8/10、失败 0、W4 排期 2**（公式计算/文档管理台）；W4 目标"8 个可跑"提前两周达成 |
| 顺手修 bug | ✅ | `_is_num` 不认 Decimal（psycopg 数值类型）→ 图表启发式失效（演示场景 1 暴露）+ 回归测试 |
| 多轮用例格式登记 | ✅ | eval 契约 0.3（冻结后可选扩展登记） |

## D5（10/1）· W2 收官 + W3 前期（A）✅

### W2 里程碑验收（对照 PLAN §13）

| W2 交付项 | 验收结果 |
|---|---|
| 编排内核 + LLM 抽象层（A） | ✅ W1 已提前交付，W2 增量至 v1.2（指代消解/槽位矩阵/DAG 并行） |
| **Prompt 三层模板定稿（D10）** | ✅ v1.0 冻结（七家族注册；agent.plan schema 演进按登记例外） |
| **缓存命中率首次统计** | ✅ 前缀命中 55.7%，节省 53% 成本（¥0.96 vs ¥2.03）；/api/usage stats 块 |
| **可演示的对话流 demo** | ✅ 覆盖度矩阵 8/10 可演示、0 失败（C 前端 + 后端全链路 SSE） |
| 全量回归（周五节奏） | ✅ **NL2SQL 31/31 + RAG 8/8 双百 + 多轮 4/4 脚本 14/14 轮** |

⚠ 未完成（B/C 侧，W2 主交付）：B 摄入流水线 v1（质量检测器/复杂度评分/MinerU/坏文档）、
C 前端联调收尾（12s 超时修复）——均无提交，已顺延进 W3 风险清单。

### W3 前期（A 已开工）：Schema Linking v0 + Join 路径注入（#4/#5 地基）

| 项 | 结果 |
|---|---|
| 表语义卡 + 双路召回 | ✅ 关键词（jieba，原始重叠计数）+ 向量（bge-small）RRF ∪ 双模态头部并集 |
| 中文业务注释 | ✅ 全部 11 业务表（跨语召回鸿沟修复：员工→employee 等） |
| FK 闭包 + 相关性约束伙伴扩展 | ✅ 连通分量桥接 + 得分排序扩展（修了两轮 bug：系统表混入/扩展顺序竞争） |
| Join 路径注入 | ✅ 选中表 FK 生成树 BFS → 显式路径入 prompt（最高度起点） |
| **消融基线（W3 交付物）** | ✅ **linking ON 31/31 = 100%（与全 schema 持平）**，平均 7.5/11 表、token -30%（0~72%）；LLM 精排（W3 正菜）预期压到 ~4 表 |
| 系统表隔离 | ✅ biz_term/kb_*/trace_*/app_session 不再进入 prompt |

### W3 剩余（A）：LLM 精排过滤干扰表 · 列级 linking · 术语库向量化在线匹配（#1/#2）· 空结果归因

⚠ 团队进度提示：B（摄入流水线 v1：质量检测器/复杂度评分/MinerU/坏文档）与 C（前端联调 +
12s 超时修复）的 W2 主交付尚未见提交——W2 剩余 4 天，建议周五复盘会前各自更新状态。


## B 增量（2026-09-23 实际执行；基于 cd45a07）

> 上文为 A 的排期周报；保留其历史风险记录。本节只更新本次实测完成的子交付。

### 只读检查与执行计划

- `pwd`：`/home/sxy/projects/multimodal-explainable-qa-agent`；`git status`：main、
  与 origin/main 一致、工作区干净；`git diff --check`：通过。
- 最近五提交：`cd45a07` / `8a3542d` / `c374166` / `15eaad1` / `c91bb16`。
- 已读 README、PLAN、B onboarding、W1/W2 周报和 IR/Trace/eval 三契约，检查
  ingestion、rag、摄入/生成脚本、用例与 runner、测试、配置和部署文件；
  已用 rg 搜索 B、W2、at risk、质量、复杂度、embedding、BGE、rerank、TODO、未完成、阻塞。
- 当前已交付：PyMuPDF/切片/混合检索 v0、四份文档、八条 RAG 用例、IR/Citation 契约冻结。
  基础闭环由 A 代劳，B 新增产品运营文档；W1 旧阻塞表须按 W1 末冻结/验收记录理解。
- P0：摄入 v1 中先交付可独立验收的 **#8 复杂度评分与解析路由**；其余质量检测器、
  MinerU、三份坏文档及修复检索闭环、知识库扩至十份继续待办。
- P1：GPU 验证、rerank、忠实度自检、公式登记。P2：RAG 专属查询扩展、完整目录识别、
  规模优化；A 已实现指代改写，不能重复把其算作 B 的未完成任务。
- 协作影响：C 的坏文档管理台/鲁棒性评测仍依赖质量及修复链路；A 后续公式消费依赖
  公式登记，模型共用依赖 GPU 验证；A 当前内核/schema linking 并未被本项阻塞。

### 本次子交付与验收

- `ingestion/complexity.py`：按 onboarding 权重产生 1–5 级和可解释依据，选择解析器；
  正常 PDF 可直抽，扫描/混合 PDF 不静默遗漏页面。
- `rag/pipeline.py`：摄入前评分与路由，写入 DocIR.meta；MinerU 不可用时在写库前报错。
- `rag/store.py`：内容未变仍刷新评估 JSONB，保留未知 metadata、chunk 和向量。
- `tests/test_complexity.py`：临时 PDF 确定性复现、规则边界、冻结 IR 保持及真实 PG 回程。
- `docs/contracts/ingestion_ir.md`：登记可选评分依据，说明检测边界和复现命令。

质量检测器验收仍为正常/坏文档缺陷报告；MinerU 验收仍为扫描件→IR→检索；
坏文档验收仍为三类告警及修复后检索；十份文档需 C 评审用例；GPU 验收需显存、
吞吐和 rerank P50/P95；公式登记需参数来源可供 A 消费。本次不宣称这些已完成。

后续模型已有明确选型 BGE-M3，无需再询问名称。当前 kb_chunk 为 512 维，目标为 1024 维：
后续应在独立 GPU 环境、确认共享 GPU 占用后验证，用独立 schema/表建立新索引并全量
评测，对照通过后再安排切换并保留旧索引回滚。本次不执行旧 onboarding 的 drop 指引。

### 环境与修改前基线

- Python 3.12.3 / PyMuPDF 1.28.2；使用已有 CPU `.venv` 与 bge-small 模型。
- Compose：backend/frontend 运行、db healthy；health：ok/db=true/LLM mock。
- `cd backend && .venv/bin/python -m pytest tests -q`：77 passed，4 个既有 warning。
- `cd backend && .venv/bin/python eval/run_rag.py`：8 例 retrieval hit@6=100%，
  answer hit=0%（mock，不代表真实模型质量）；失败检索用例无。
  逐条耗时 rag-001..008：235/8/11/9/11/8/11/10 ms。
  报告：`backend/var/eval/rag_20260923_104822.json`（运行产物，不提交）。


### 修改后验证（本次实际结果）

以下 Python 命令均在 `backend/` 下执行：

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m pytest tests/test_complexity.py tests/test_ingestion.py tests/test_rag.py -q` | 41 passed（含新增 30 项） |
| `.venv/bin/python -m pytest tests -q` | 最终 107 passed，11.27s；4 个既有 warning，无 skip/failure |
| `.venv/bin/python scripts/ingest_docs.py` | 4 docs，0 new chunks；原有 38 chunks 保留；local/512 维 |
| `.venv/bin/python eval/run_rag.py` | 测试临时文档清理后独立评测：8/8 retrieval hit@6；mock answer hit=0% |
| `.venv/bin/python scripts/smoke_rag.py` | 38 chunks，retrieval recall@6=8/8；两条脚本化生成及 Citation/Trace 正常 |

最终报告：`backend/var/eval/rag_20260923_105834.json`，不提交运行产物。
rag-001..008 耗时分别为 **354/11/8/9/9/7/8/8 ms**，检索失败用例无。
未切换真实 LLM，故本次不能宣称真实答案忠实度通过。

只读 SQL 确认四文档 `meta.complexity=1`，chunk 数为 employee_handbook=15、
product_operations=10、sales_review_2025=7、service_sop=6；无测试文档残留。
正常文档和所有异常 PDF 的结果可通过新增测试复现，扫描/混合 PDF 为 5 级，
空白页为 4 级，长表格文档为 3 级。此为复杂度评估，不等同于质量缺陷检测。

格式工具隔离安装在 `/tmp/mqa-b-format-tools`（Ruff 0.16.8），未修改 CPU `.venv`。
仓库根执行并通过：

```bash
/tmp/mqa-b-format-tools/bin/ruff format --check backend/app/ingestion/complexity.py backend/tests/test_complexity.py
/tmp/mqa-b-format-tools/bin/ruff format --check --range 41-69 backend/app/rag/pipeline.py
/tmp/mqa-b-format-tools/bin/ruff format --check --range 121-140 backend/app/rag/store.py
/tmp/mqa-b-format-tools/bin/ruff check backend/app/ingestion/complexity.py backend/tests/test_complexity.py
/tmp/mqa-b-format-tools/bin/ruff check --select E9,F63,F7,F82 backend/app/rag/pipeline.py backend/app/rag/store.py
git diff --check
git status --short
git --no-pager diff --stat
```

已有文件仅对本次变更函数做格式检查；不对队友未修改代码做全文件风格重排。
本增量包含 4 个既有文件修改和 2 个新增文件；运行报告、模型及环境文件不纳入版本控制。
`git diff --stat` 默认不包含两个未跟踪新文件，审查时需一并查看。

## B 增量（2026-09-27）：#9 文档质量评估 rules-v1

> 本节是在 2026-09-23 已交付复杂度评分与解析路由基础上的后续增量。
> 上文未完成/风险描述保留为历史记录，以本节作为当前最新状态。

### 本次交付

- 新增 `backend/app/ingestion/quality.py`，提供可解释的 PDF 质量评估：
  页面方向、文本行倾斜、渲染清晰度、文本层覆盖和保守繁体字符信号。
- 新增文档级 `score`、逐页 `page_reports`、`textless_pages`、
  `rotated_pages` 和结构化 `warnings`，页码保持 1-based。
- `rag.pipeline.ingest_document` 在复杂度评分与解析前执行质量检测，
  将结果写入 `DocIR.meta.quality`。
- `rag.store.update_assessment` 合并刷新 `quality`、`complexity` 和
  `complexity_details`，内容未变化时保留 chunk ID 和 embedding。
- 损坏、加密 PDF 明确拒绝，不删除或覆盖已有索引。
- 新增 `backend/tests/test_quality.py`，覆盖正常、旋转、扫描、模糊、
  简繁误报、元数据契约、损坏、加密和 Pipeline 接入共 9 项测试。
- 更新 `docs/contracts/ingestion_ir.md`，登记 rules-v1 字段、算法边界、
  元数据形状和独立复现方法。

### 验收结果

| 验收项 | 结果 |
|---|---|
| 质量检测专项测试 | `9 passed` |
| 既有复杂度/摄入/RAG 回归 | `41 passed` |
| 全量测试 | `130 passed`，5 warnings，0 failure |
| 正常知识库文档 | 4/4 均无旋转、无倾斜、具备文本层、无繁体误报 |
| 幂等摄入 | 4 docs、38 chunks、0 new chunks |
| 向量保留 | PostgreSQL 中 38/38 chunks 保留 embedding |
| RAG 正式评测 | 8/8，retrieval hit@6 = 100% |
| RAG 冒烟 | retrieval recall@6 = 8/8；Citation/Trace 正常 |

四份正常文档的质量结果：

| doc_id | clarity | score | complexity |
|---|---:|---:|---:|
| employee_handbook | 0.9707 | 0.9912 | 1 |
| product_operations | 0.9824 | 0.9947 | 1 |
| sales_review_2025 | 0.9843 | 0.9953 | 1 |
| service_sop | 0.9745 | 0.9923 | 1 |

正式 RAG 报告：
`backend/var/eval/rag_20260927_123920.json`（运行产物，不提交）。
当前为 mock LLM，因此 answer hit=0% 仅代表未验证真实生成答案质量，不代表检索失败。

### 能力边界与剩余任务

- 当前方向检测读取 PDF rotation metadata，不推断缺少方向元数据的扫描图片方向。
- 当前倾斜检测依赖原生文本行，扫描件仍需 OCR/图像级倾斜检测。
- 清晰度为 Laplacian 启发式指标，不等价于 OCR 字符准确率。
- 繁简检测使用保守繁体专属字符集，不承担全文简繁转换。
- 本次完成质量检测器，不宣称摄入流水线 v1 整体完成。
- 尚待：MinerU/PaddleOCR 实际解析、三类坏文档生成与修复检索闭环、
  知识库扩至 10 份、GPU/BGE-M3/rerank、忠实度自检和公式登记。


## B 增量（2026-09-27）：三类坏文档测试资产

### 本次交付

- 新增 `backend/scripts/gen_bad_docs.py`，程序化生成3类固定坏文档。
- 新增 `backend/tests/test_bad_docs.py`，验证文件有效性、缺陷信号、
  解析路由和结果确定性。
- 新增 `data/docs_bad/`，与正常 `data/docs_raw/` 隔离，避免被默认摄入脚本误收录。
- 目录结构缺失样本保留原生文本但不生成可靠 H1～H4。
- 旋转扫描和模糊繁体扫描均无原生文本层，复杂度评分为5并路由至 MinerU。
- 模糊繁体样本在 OCR 前不声称检测到繁体，符合当前能力边界。

### 验收结果

| 文档 | 质量信号 | 复杂度/路由 |
|---|---|---|
| bad_missing_structure | clarity=0.9616，score=0.9885，无质量告警，无可靠章节层级 | level 1 / pymupdf |
| bad_rotated_scan | textless_pages=[1]，orientation=90，rotated_pages=[1] | level 5 / mineru |
| bad_blurred_traditional | clarity=0.0254，textless_pages=[1]，low_clarity | level 5 / mineru |

| 测试项 | 结果 |
|---|---|
| 坏文档专项 | 5 passed |
| 坏文档+质量+复杂度+摄入 | 49 passed |
| 全量测试 | 135 passed，5 warnings，0 failure |

### 当前边界

本次完成坏文档生成与检测基准，不包含自动修复。下一步是独立部署 MinerU，
实现扫描件解析适配器，并将旋转、模糊繁体样本恢复为现有 `DocIR` 后重新检索。
