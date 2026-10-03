# W5 进度

## C 收口与安全检查（2026-10-03）

### 交付

- 文档管理台完成上传、质量报告、修复、原件/修复件预览闭环；上传后自动刷新并选中新文档。
- 前端兼容测试 **9/9**（SSE 4 + 文档 API 5），`vue-tsc` 通过。
- 后端安全边界回归新增 `backend/tests/test_api_security.py`，覆盖输入校验、Trace 分页上限和修复并发门禁。
- Trace 契约正文已与 A 当前实现对齐：`turn_id`、实时 `answer.delta`、`error.code/recoverable` 和回放端点均有明确说明。

### 安全检查与修复

| 风险 | 处理 |
|---|---|
| 超长或特殊 `session_id` 导致内存/账本膨胀 | `ChatRequest.session_id` 限制 1–128 字符并限定安全字符集 |
| `doc_filter` 未限制输入 | 限制 128 字符并限定文档 ID 字符集 |
| Trace 列表 `limit` 可被放大 | `GET /api/trace` 限制 1–100 条 |
| OCR/MinerU 修复并发占满 CPU/GPU | 每个 API 进程单任务门禁，繁忙时返回 HTTP 429 |
| PDF 路径穿越、非 PDF、加密 PDF、上传过大 | B 既有校验保持并由 C 管理台兼容测试覆盖 |

剩余部署级风险：生产环境仍需在反向代理或网关增加鉴权、限流和 HTTPS；本地演示 API 不内置账号体系。

## B-D1（10/2）· RAG 忠实度自检 ✅

### 交付内容

- 新增 `rag.factcheck` Prompt 家族并按 Prompt 契约首次合入即冻结；
- RAG 流程升级为“混合检索 → 引用生成 → 逐句引用支撑检查”；
- 检查器仅依据检索片段判断事实是否有依据；
- 发现无依据事实、错误引用或夸大陈述时执行收敛重写；
- 最多检查 2 轮，防止无限调用；
- 非法 JSON、LLM 异常、空重写和不变重写均优雅降级，不中断原 RAG；
- 默认 `RAG_FACTCHECK_ENABLED=0`，避免普通测试误调用付费模型；
- `RAGResult` 保留 `initial_answer` 与最终 `answer`，支持前后对照；
- Trace 新增 `rag_factcheck` step，并记录每轮 `rag.factcheck` LLM 调用；
- `eval/run_rag.py` 增加初始/最终答案、faithfulness、改写、降级和 Trace 审计字段。

### 测试与真实模型验收

| 验收项 | 结果 |
|---|---:|
| factcheck + RAG 专项测试 | 14/14 |
| 后端全量回归 | 187/187 |
| DeepSeek 定向对抗案例 | 3/3 |
| 定向案例最终 faithful | 3/3 |
| 错误事实修复 | 15 天 + 500 元补贴 → 10 天年假 |
| 错误引用修复 | 5%[1] → 3%[2] |
| 定向门禁调用成本 | ¥0.004130 |
| 8 条 RAG retrieval hit@6 | 8/8 |
| 8 条 RAG answer hit | 8/8 |
| 8 条 RAG faithful | 8/8 |
| 8 条 factcheck Trace | 8/8 |
| 8 条 degraded | 0 |
| 初始答案一致性 | 8/8 |
| 现有用例改写数 | 0/8 |

现有 8 条 RAG 答案本身均有检索证据支撑，因此全部一轮通过且无需重写；
定向对抗案例证明了系统面对错误事实和错误引用时能够触发第二轮并完成修复。

审计回放启用了应用响应缓存，缓存回放平均延迟为
`27.50ms（off）→ 40.12ms（on）`，该数值仅证明缓存路径和 Trace
接入正常，**不得解释为未缓存生产环境的真实 factcheck 延迟开销**。

### 能力边界

- 忠实度判断本身依赖 LLM，因此保留 `degraded` 和人工复核语义；
- 当前 8 条常规 RAG 用例没有包含幻觉答案，纠错能力由 3 条定向对抗案例覆盖；
- 默认关闭功能，生产或正式评测时通过环境变量显式启用；
- `.env` 与真实 API Key 不进入 Git。

## B-D2（10/3）· 摄入期公式自动登记 ✅

### 交付内容

- 新增并冻结 `rag.ingest` Prompt 家族，只依据候选原文块抽取公式；
- 摄入阶段在切片完成后、向量化与落库前生成 `FormulaIR`；
- 校验来源 block、变量、参数集合、单位、参数来源和表达式安全性；
- 使用现有 `FormulaEngine` 执行 SymPy 与受限执行双通道验证；
- `kb_formula` 按 `doc_id` 原子替换，并与文档、切片共享事务；
- 只有 `replace_ready=true` 才替换旧公式，失败或部分拒绝均保留旧值；
- 默认 `FORMULA_EXTRACT_ENABLED=0`，避免普通摄入误调用付费模型；
- 配置已同步到 `.env.example` 和 Docker Compose。

### 测试与真实模型验收

| 验收项 | 结果 |
|---|---:|
| 公式抽取、持久化、公式引擎和 RAG 专项测试 | 28/28 |
| 公式引擎独立回归 | 9/9 |
| 后端全量回归 | 204/204 |
| 真实 PDF | `employee_handbook.pdf` |
| 候选块 / 有效公式 / 拒绝项 | 6 / 1 / 0 |
| DeepSeek 调用次数 | 1 |
| Prompt / Completion tokens | 917 / 68 |
| 调用成本 | ¥0.002378 |
| 双通道数值验证 | 4/4 |

真实 DeepSeek 抽取公式为 `0.03 * S + 0.02 * max(S - 100000, 0)`。
参数 `S` 为“当月个人销售额”，单位为“元”，来源为 `db`。
页面及面包屑准确指向“薪酬与提成 > 销售提成计算办法”。

| S（元） | 期望提成（元） | 双通道结果 |
|---:|---:|---:|
| 80,000 | 2,400 | 2,400 |
| 100,000 | 3,000 | 3,000 |
| 150,000 | 5,500 | 5,500 |
| 250,000 | 10,500 | 10,500 |

### 安全边界

- LLM 只生成结构化候选，不能绕过本地表达式和参数校验；
- 非法 JSON、未知变量、不安全表达式和未知 block 均不会入库；
- 抽取失败和部分拒绝不会覆盖数据库中已有的有效公式；
- DeepSeek 门禁仅验证抽取结果，没有修改 `kb_formula`；
- `.env`、API Key、评测报告和运行时数据库不进入 Git。

## B-D3（10/3）· 知识库扩至 10 份 ✅

### 交付内容

- 正常知识库由 4 份扩展到 10 份，3 份坏文档继续隔离在
  `data/docs_bad/`，不计入正常语料数量；
- 新增供应商协议、营销活动复盘、财务制度、版权规范、会员规则和
  歌单运营规范 6 份 PDF；
- 新文档与 Chinook 的 Customer、Invoice、Artist、Album、Track、
  Playlist 和 Genre 等实体建立语义联系；
- PDF 生成器新增 ReportLab 结构化表格支持，供应商分级、费用审批和
  会员等级均以真实表格写入 PDF 文本层；
- 财务制度包含渠道服务费、净结算额和预算执行率 3 条明确公式，
  作为后续多公式 FormulaIR 压测材料；
- 10 份 PDF 均保留标题与 H1/H2 层级，可稳定解析为 DocIR 和 ChunkIR；
- 新增文档级严格检索门禁：目标文档必须为 Top-1，且关键事实必须来自
  目标文档自身的命中 chunk；
- `rag_corpus_w5.jsonl` 已挂入 `run_levels.py`，L1/L2 自动包含新增 6 例，
  L0 继续保持原有 5 例快速门禁。

### 验收结果

| 验收项 | 结果 |
|---|---:|
| 正常 PDF 数量 | 10 |
| 新增正常 PDF | 6 |
| 结构化表格 | 3 |
| 正常文档 ChunkIR | 116 |
| 新增文档 ChunkIR | 78 |
| 10 份 PDF 标题层级解析 | 10/10 |
| 新增文档严格 Top-1 检索 | 6/6 |
| 全量 RAG retrieval hit@6 | 41/41 |
| 全量 RAG strict gate | 41/41 |
| 后端全量回归 | 208/208 |
| 本次付费 API 调用 | 0 |

新增 6 份文档在关闭 reranker 的本地
`bge-small-zh-v1.5` 512 维 CPU 配置下仍全部 Top-1 命中，说明结果不是
依赖精排器兜底。原有 `employee_handbook-f1` 公式记录在重摄入后保持不变。

### 安全与能力边界

- 默认使用 mock LLM 且关闭公式抽取，本次扩库与检索验收没有产生 API 费用；
- mock 运行下 `answer hit=0%` 属预期，仅用于验证检索和流水线，不代表真实
  DeepSeek/Qwen 的答案质量；
- 财务制度中的 3 条公式已进入 PDF 和 ChunkIR，但本次没有将其自动写入
  `kb_formula`；公式自动登记能力已由 B-D2 的真实 DeepSeek 门禁独立验证；
- 数据库备份、评测报告、日志、模型和 `.env` 均保存在 Git 工作区之外；
- 语料为比赛演示用的受控业务文档，不应解释为真实公司的法律或财务制度。

## B-D4（10/3）· 查询改写 ✅

### 交付内容

- 新增并冻结 `rag.query_rewrite` Prompt 家族，不修改任何已有冻结 Prompt；
- `agent.rewrite` 继续负责指代消解与自包含问题，检索改写只优化进入 RAG 的问题；
- 一次有界调用返回严格 JSON，保留实体、条件、时间和比较关系；
- history 默认取最近 6 条，配置范围 0–20，单条送入模型最多 500 字符；
- `/api/rag` 新增可选 history（最多 20 条，每条最多 2000 字符，仅 user/assistant），
  HTTP 422 边界测试覆盖非法输入；
- Agent 单工具与 HYBRID 子任务均传递 history，API/工具层传递 doc_filter；
- 修复 doc_filter 在全库候选截取后才过滤的问题，避免指定文档被候选上限遗漏；
- 默认 `RAG_QUERY_REWRITE_ENABLED=0`、`RAG_HYDE_ENABLED=0`，不增加默认模型调用；
- HyDE 仅用于 Dense embedding；BM25/reranker 使用 retrieval_query，
  generation/factcheck 继续使用输入问题和真实 chunk，假设文档不进入引用；
- 非法 JSON、缺字段、空查询、类型错误、超长输出及 LLM 异常均回退原查询，
  清空 HyDE 并标记 degraded，不中断 RAG；
- `RAGResult`、API 和工具结果提供 retrieval_query/query_rewrite 审计元数据；
- 启用时 Trace 新增 rag_query_rewrite step 和调用用量子节点，
  记录改写前后查询、状态、原因及 HyDE 长度/160 字符摘要；关闭时保持原节点顺序。

### 验收结果

| 验收项 | 结果 |
|---|---:|
| 新增查询改写专项用例 | 43/43 |
| 查询改写 + RAG + factcheck + API 安全专项 | 61 passed, 3 warnings |
| 后端全量回归 | 251 passed, 6 warnings |
| 指定文件 py_compile | 通过（含 Agent Kernel） |
| 本次付费 API 调用 | 0 |

测试仅使用 `LLM_PROVIDER=mock`，全量回归使用本地 CPU embedding，
关闭 reranker、公式抽取、factcheck 与全局查询改写；新功能启用行为由
专项测试的独立 mock 配置验证。无跳过项；warnings 为既有依赖弃用和
test_connection 返回非 None 的提示。历史 B-D1/B-D2/B-D3 测试数字保持不变。

### 能力边界

- mock 门禁验证解析、参数传递、检索通道隔离、失败回退与 Trace 契约，
  未验证真实模型的检索质量提升；真实模型验收需另行授权；
- HyDE 依赖查询改写开关，单独启用 HyDE 不会触发模型调用；
- 空 HyDE 回退为 retrieval_query 的 Dense embedding；
- `.env`、密钥、模型、报告和数据库产物不进入变更；尚未 commit 或 push。
