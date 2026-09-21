# C 角色上手文档（全栈前端 / 评测体系 / 技术文档）

> 写给你的人：A（2026-09-23）。你负责的三块——前端、评测、文档——里，
> 评测的雏形和全部素材我已经备好，前端还是占位页。**先通读本文档再动手**，
> 遇到与文档不符的地方以代码/实际响应为准并回来改文档。
>
> 阅读时间约 25 分钟，跟做约 40 分钟。

---

## 0. 你负责什么（PLAN §12）

| 模块 | 说明 | 周期 |
|---|---|---|
| **Vue 前端** | 对话流(SSE) / 推理链路 DAG / 数据卡片(表格+图表) / 引用溯源(PDF 定位) / 澄清交互 / 文档管理台(W4) | W2 主体，W4 补管理台 |
| **评测体系** | 分级跑分脚本 L0/L1/L2 · 用例集扩到 140+ · 用量记账报表 | W3 主体（雏形已就绪，见 §3.4） |
| **技术文档** | 设计报告(≤20页) + 技术实现说明书(≤30页)，W3 起骨架，你牵头 | W3-W5 |
| **演示** | 10 个场景脚本（PLAN §9 已列）+ 视频 | W5 |

**你现在就有一件 P0 事**：评审 Trace 契约（阻塞项 #2，期限周四晚）——
`docs/contracts/trace.md` 的 §4.2 SSE 事件格式就是为你写的，你说不够用就得改。
这是 W2 前端开发的地基，见 §5.1。

---

## 1. 十分钟跑起来

```bash
# 1) 后端全栈（PG + 后端 + 前端占位 + 数据 + 冒烟，幂等）
./deploy/quick_start.sh

# 2) 验证编排对话（真实 deepseek，已配好 key）
curl -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"question": "销量前十的曲目是哪些", "session_id": "c1"}' | python3 -m json.tool | head -30

# 3) 验证 SSE 流（你前端要消费的原始事件流）
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"question": "一共有几种曲风", "session_id": "c2"}'

# 4) 前端开发环境（宿主机已有 node v22 + npm，registry 可直连）
cd frontend && npm create vite@latest . -- --template vue-ts   # W2 第一件事，见 §5.1
```

后端地址约定：dev 用 Vite proxy 把 `/api` 转发到 `http://localhost:8000`（推荐，
免 CORS 配置）；生产镜像由 `frontend/Dockerfile` 构建（当前是占位 nginx，W2 改多阶段构建）。

---

## 2. 系统地图（C 视角）

```
你要消费的 API（backend/app/main.py，全部实测可用）
  POST /api/chat          编排对话（JSON）：意图路由 + 工具调度 + trace 树
  POST /api/chat/stream   同上但 SSE 流式（事件契约见 §3.2）★ 前端主入口
  POST /api/nl2sql        裸问数流水线（含 rewritten/sql/rows/chart_hint）
  POST /api/rag           裸 RAG 流水线（含 citations[]）
  GET  /api/health        {status, db, llm_provider, llm_model}
  GET  /api/schema        11 张表的列/类型/行数（可做 schema 浏览器）
  GET  /api/terms         术语库词条（别名展示素材）
  GET  /api/usage         LLM 用量账本汇总（成本看板数据源！）

你的契约文档（docs/contracts/，你是评审方）
  trace.md            ★ SSE 7 类事件 + TraceNode 字段 + detail.key 约定——你必须评审
  trace_schema.json   Trace 树 JSON Schema（权威）
  eval_case_format.md 用例格式 + 双指标口径（你 W3 扩用例按这个来）
  prompt_static_layer.md / ingestion_ir.md  了解即可（A/B 的）

已有素材（写文档直接引用）
  PLAN.md                     总方案（评分表拆解、里程碑、分工）
  docs/llm_selection_report.md 选型报告（技术文档附录素材，含成本数据）
  docs/design/agent_kernel.md  编排内核设计（架构图素材）
  docs/weekly/W1_progress.md   周报（含阻塞项清单）
```

---

## 3. 已为你完成的东西（每件的形状）

### 3.1 编排对话 JSON（POST /api/chat 响应，实测）

```json
{
  "question": "销量前十的曲目是哪些",
  "answer": "销量最高的曲目是 The Trooper，共售出 5 份；…",
  "intent": "DB_QUERY",              // CHAT|DB_QUERY|DOC_QUERY|HYBRID|AMBIGUOUS
  "status": "ok",                    // ok|clarify|degraded|error
  "data": {                          // 工具载荷（DB_QUERY 时）
    "sql": "SELECT …", "columns": ["track_name","total_quantity"],
    "rows": [["The Trooper", 5], …], "row_count": 10,
    "chart_hint": "bar",             // bar|line|null → ECharts 类型
    "status": "ok"
  },
  "citations": [],                   // DOC_QUERY/HYBRID 时：见 §3.3
  "clarify": {},                     // status=clarify 时：{missing_slots[], options{}}
  "cost_rmb": 0.0029,                // 本轮成本（Trace 聚合）
  "latency_ms": 3877,
  "trace": { "root": { "children": [ … ] } }   // 完整 Trace 树
}
```

**Trace 树形态**（前端 DAG/时间线的唯一数据源）：DB_QUERY 一轮的节点序列实测为
`intent → nl2sql → (rewrite → schema_context → sql_gen → sql_validate → sql_execute → summarize)`，
工具调用内嵌流水线子树——时间线视图按 `latency_ms` 排，DAG 视图按 `parent_id` 连边。

### 3.2 SSE 事件流（POST /api/chat/stream，实测样本）

```
event: turn.start
data: {"question": "一共有几种曲风", "session_id": "c2", "resumed_clarify": false}

event: trace.node
data: {"id": "…-n1", "parent_id": "…", "type": "intent", "label": "intent",
       "input": "…", "output": {"intent": "DB_QUERY", "confidence": 0.95},
       "latency_ms": 849, "status": "ok",
       "detail": {"slots": {...}, "missing_slots": []}, "started_at": "…"}

event: trace.node            ← 每个节点完成推一条（nl2sql/rewrite/…）
event: answer.delta          ← v1 为整段答案（W2 升级为真流式，契约不变）
event: answer.done           ← 全量 payload（= §3.1 的 JSON），渲染锚点
event: turn.end              ← {latency_ms, cost_rmb, status}
```

另外两类事件按需出现：`clarify.request`（携带 `missing_slots[]` + `options{}`）→ 渲染选项按钮；
`error`（不可恢复错误）。

**消费要点（重要坑）**：
- 浏览器 `EventSource` **只支持 GET**，这里是 POST → 用 `fetch` + `ReadableStream` 手工解析，
  或直接用 `@microsoft/fetch-event-source` 库
- `answer.done` 是渲染锚点（数据齐全）；`trace.node` 用于实时时间线/DAG 增量构建
- `trace.node` 的 `parent_id` 保证父节点先到；断线不重放（契约 §4.2：凭 turn_id 拉全量树——
  **注意：`GET /api/trace/{turn_id}` 契约里写了但尚未实现**，你需要就找 A，半小时的活）

### 3.3 引用溯源数据（DOC_QUERY / HYBRID）

```json
"citations": [
  {"doc": "Chinook 唱片员工手册", "doc_id": "employee_handbook", "page": 2,
   "breadcrumb": "Chinook 唱片员工手册 > 假期制度 > 年假",
   "snippet": "入职满一年的员工每年享有 10 天年假；…", "score": 0.031}
]
```

点击引用 → 打开对应 PDF 第 N 页并高亮 snippet（PDF.js）。**PDF 文件本体目前没有 HTTP 服务**
（在 `data/docs_raw/*.pdf`，3 份已入 git）——需要向 A 提需求 `GET /api/docs/{doc_id}/pdf`（见 §7）。

### 3.4 评测雏形（你的 W3 地基，已能跑）

| 资产 | 位置 | 现状 |
|---|---|---|
| 用例集 | `backend/eval/cases/` | 47 个：单表 10 + 多表 21 + 选型 10 + RAG 6 |
| NL2SQL runner | `backend/eval/run_nl2sql.py` | 语义 4 级匹配 + strict 双口径；**当前 L1 全量 100%（31/31）** |
| RAG runner | `backend/eval/run_rag.py` | retrieval_hit / answer_hit 双指标（定位失败层） |
| 用例预检 | `backend/eval/preflight_cases.py` | 你加用例必过此门（可执行/行数预算/确定性/过校验器） |
| 选型评测 | `backend/eval/model_selection.py` | 已出报告；RAG 维度待 B 重建知识库后补 |
| 成本账本 | `GET /api/usage` + `backend/var/llm_usage.sqlite` | 每 API 调用落账（tokens/前缀缓存命中/费用） |

**你要建的三级跑分**（PLAN §11.2，本质是给现有 runner 加 `--level`）：
L0 smoke（每类 5 用例，<¥0.5，改 prompt 后跑）/ L1 regression（全量 ~15min ~¥5-10，每日收工）/
L2 full（全量×主备×3 重复，里程碑跑）。结果落 `var/eval/` + 趋势图 → 直接进技术文档。

### 3.5 前端现状

`frontend/` 只有两个文件：占位 `index.html` + nginx `Dockerfile`（8090 端口，compose 里已绕过
entrypoint 挂起问题）。**W2 你从 Vite 脚手架开始重建这个目录**，技术栈定案 D11：
Vue 3 + TS + Vite + **Naive UI**；DAG 用 AntV X6，图表 ECharts，PDF 用 PDF.js。

---

## 4. 命令速查

```bash
# 后端 API 快探
curl -s localhost:8000/api/health
curl -s localhost:8000/api/usage | python3 -m json.tool | head -12
curl -s -X POST localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"question":"你好","session_id":"t1"}' | python3 -m json.tool

# 多轮演示（同 session_id 连发三轮，看澄清与上下文）
for q in "销售额增长率是多少" "对比 2024 年和 2023 年全年" "那 2023 呢"; do
  curl -s -X POST localhost:8000/api/chat -H 'Content-Type: application/json' \
    -d "{\"question\":\"$q\",\"session_id\":\"demo\"}" | python3 -c \
    "import json,sys; d=json.load(sys.stdin); print(d['intent'], d['status'], d['answer'][:60])"
done

# 评测（C 的地盘）
cd backend
.venv/bin/python eval/run_nl2sql.py                       # 全量 L1（31 用例）
.venv/bin/python eval/run_nl2sql.py --only-ids st-001,mt-002   # L0 子集就是这么简单
.venv/bin/python eval/run_rag.py                          # RAG 6 例（B 重建知识库前 retrieval 会降级）
.venv/bin/python eval/preflight_cases.py <cases.jsonl>    # 新用例门禁

# 前端
cd frontend && npm install && npm run dev                 # Vite 5173，配 proxy → 8000
docker compose -f deploy/docker-compose.yml --env-file .env --project-name mqa up -d frontend
```

---

## 5. 你的 W2 任务清单（按优先级，含验收标准）

### P0-A：Trace 契约评审（本周四晚前，阻塞项 #2）

逐条核对 `trace.md` §4.2：7 类事件够不够渲染？`trace.node` 的字段够不够 X6 画图
（缺什么：层级深度提示？节点分类图标映射？）；`answer.done` payload 与你的组件 props 对不对得上。
**有意见周四晚前提，周五冻结**；没意见在变更记录里签字。

### P0-B：前端骨架 + 对话流（PLAN W2：对话流 SSE + SQL/引用展示 + 多轮会话）

1. **Vite 脚手架**：Vue 3 + TS + Naive UI；目录建议 `src/{api,components,stores,views}`；
   `vite.config.ts` 配 `/api` proxy → `localhost:8000`
2. **对话流页面**：消息列表（user/assistant）+ 输入框 + session_id 管理（localStorage）；
   SSE 消费（fetch-event-source）；`answer.done` 渲染完整消息
   - 验收：对话、问数（SQL+表格+图表卡片）、RAG（答案+引用列表）三类消息可渲染
3. **数据卡片**：`data.columns/rows` → Naive UI DataTable；`chart_hint` → ECharts（bar/line）
4. **引用渲染**：`citations[]` → 消息尾部引用列表（面包屑 + 页码），点击行为先做锚点
   （PDF 预览等 §7 的后端端点）
5. **澄清交互**：`clarify.request` → 选项按钮组（options 为空时降级为文本输入框——
   注意当前模型输出的 options 常为空 `{}`，务必兼容）；用户点选 → 作为下一轮 question 发送
   （同 session_id，后端靠历史恢复，前端无需额外状态）
   - 验收：跑通"增长率是多少"→ 按钮/输入 → 正确续查
6. **多轮会话**：连续 5 轮上下文保持（后端已就绪，前端只需透传 session_id）

### P1：推理链路 DAG（PLAN W2-W4：X6 双视图）

- 时间线视图（W2）：`trace.node` 事件增量追加，按 `latency_ms` 展示耗时
- DAG 视图（W2 末-W4）：`parent_id` 连边；布局用 dagre 分层；节点类型 → 图标/颜色映射
  （intent/plan/tool_call/llm_call/fuse/clarify/step）；点击节点展示 input/output/detail 侧栏
- 验收：10 个演示场景之一"推理链路 DAG 全程回放"可跑（用 answer.done 里的 trace 全量渲染即可）

### P1：生产构建

`frontend/Dockerfile` 改多阶段：`node:22-alpine` build → `nginx:alpine` serve + `/api` 反代
`backend:8000`（compose 里 backend 服务名）。占位页直接删。
（镜像拉取走 `.env` 的 `DOCKER_REGISTRY_MIRROR`，见 §6）

---

## 6. W3 任务预告（评测体系 v1 + 文档骨架）

1. **三级跑分脚本**：现有 runner 加 `--level L0|L1|L2`（L0 = 每类抽样 5 个；
   L2 = × 主备两 provider × 3 重复）+ 结果趋势图（matplotlib 或前端页面）+ 每周五 L1 例行
2. **用例扩充到 140+**（PLAN §6.2 路线）：
   - 鲁棒性变体 ×30：拿现有用例做错别字/别名/口语化变换（术语库 `GET /api/terms` 里的
     别名是现成素材，"帝都/摇滚/mp3"）；标注基线用例 id 便于对比
   - 澄清 ×30：缺要素问题（A 的 `clarify` 场景已可跑通，评估"澄清触发准确率 + 澄清后完成率"）
   - 多轮脚本 ×8：5+ 轮对话脚本（JSONL 里一个用例=一个 session 的多问）
   - 跨源 ×10：等 B 知识库重建后（HYBRID 链路已通）
   - **每批新用例过 `preflight_cases.py` 门禁**
3. **用量/成本报表**：`GET /api/usage` 数据 + 前端成本看板页（周五复盘贴周报；预算管控叙事素材）
4. **技术文档骨架**：设计报告（≤20 页，含"核心亮点预览页"）+ 说明书（≤30 页）目录先立起来，
   素材从 §2 列的文档里搬；按初赛评分表的 10 分文档项对齐

---

## 7. 协作接口（你需要向 A/B 提的需求，别客气）

| # | 需求 | 找谁 | 说明 |
|---|---|---|---|
| 1 | `GET /api/docs/{doc_id}/pdf` 静态文档服务 | A（半小时） | 引用溯源 PDF 预览的前提；文件在 `data/docs_raw/` |
| 2 | `GET /api/trace/{turn_id}` 拉全量树 | A（契约已写未实现） | 断线恢复/历史回放 |
| 3 | answer.delta 真流式（逐 token） | A（W2 联调时） | 目前整段推送，打字机效果是假流式 |
| 4 | clarify.options 稳定产出 | A（prompt 调优） | 当前常为空，前端已按兼容空值设计 |
| 5 | 文档管理台 API（上传/质量报告/修复） | B（W4 摄入流水线） | 前端文档管理台的对接面，提前对齐格式 |
| 6 | RAG 知识库重建 | B（按 B_onboarding §1） | RAG 消息演示与 RAG 用例跑分的前提 |

**别人依赖你的**：Trace 契约冻结（你是评审方）；周五 L1 回归跑分 + 复盘节奏（你主持）；
演示视频脚本分镜（W5）。技术文档你牵头，A/B 每完成一个中级任务当周交你该章节初稿（PLAN §12 约定）。

---

## 8. 踩坑记录

- **SSE 用 POST**：EventSource 不可用（§3.2）；Nginx 反代需加
  `proxy_buffering off`（compose 的 X-Accel-Buffering 响应头后端已带）
- **网络**：npm registry 可直连；Docker Hub 需走 `.env` 里的 `DOCKER_REGISTRY_MIRROR`
  （daocloud）；前端镜像构建的 `FROM` 已参数化，加基础镜像记得用 build arg `REGISTRY`
- **宿主 Node**：v22.23.2（`~/.local/node`），够 Vite；不需要容器化开发
- **8090 端口**是前端容器映射口（8080 被占）；开发期建议直用 Vite 5173 + proxy
- **`clarify.options` 常为空**：模型输出不稳定，前端必须兼容（按钮组→文本框降级）
- **金额显示**：`cost_rmb` 单位是元，量级 0.001-0.01/轮，看板注意精度格式化
- **`docs/weekly/W1_progress.md`** 里的阻塞项清单每周一/五更新，你的评审任务在 P0 #2

---

## 9. 第一天建议（照做即可）

1. [ ] 跑通 §1 的四条命令，重点看 SSE 原始事件流长什么样
2. [ ] 读 `docs/contracts/trace.md` 全文 + `trace_schema.json`（15 分钟）
3. [ ] 用 §4 的"多轮演示"命令跑一遍澄清场景，理解 session_id 驱动的恢复机制
4. [ ] 在 trace.md 提交你的评审意见（或确认无意见）——**这是 P0，别拖过周四**
5. [ ] Vite 脚手架搭起来，先做对话流页面（§5 P0-B），当天能看到第一条 SSE 消息渲染

有疑问群里 @A（后端/编排/评测 runner）或 @B（RAG/文档摄入）。
