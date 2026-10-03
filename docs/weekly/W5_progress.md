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
