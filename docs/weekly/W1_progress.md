# W1 进度日志（9/21-9/27）· A 角色周报

## D1（9/21）· A 角色完成 ✅

### 交付物

| 项 | 状态 | 说明 |
|---|---|---|
| 项目骨架 + docker-compose + quick_start.sh | ✅ | PG(pgvector:pg16) + backend + frontend 占位；一键启动含 Chinook 导入与冒烟，幂等可重复执行 |
| Chinook PG 版导入 | ✅ | 11 表 / 3503 曲目 / 2240 订单行；表列名统一小写（PG 无引号折叠兼容，DDL 见 `data/db_schema/chinook_pg.sql`） |
| 术语库抽取脚本 v1 | ✅ | 129 词条自动入库（genre/mediatype/playlist/country/city/firstname）+ 8 条种子别名（帝都类）增量合并 |
| LLM 抽象层 v0 | ✅ | provider 纯配置切换（deepseek/qwen/siliconflow/mock）；SQLite 响应缓存（hash 模板版本化）；用量记账（tokens/前缀缓存命中/折算¥）；`GET /api/usage` 可查 |
| 单表 NL2SQL 最小闭环 | ✅ | 改写(术语链接)→schema上下文→双步生成→sqlglot校验→只读执行→自修复(≤3)→总结+图表建议；全流程 Trace |
| Trace JSON Schema 契约 v0.1 | ✅ 草案 | `docs/contracts/trace_schema.json` + `trace.md`；运行时与 Schema 枚举同步有测试守护；**待 B/C 评审后周五冻结** |
| eval 契约（NL2SQL 部分）+ 10 用例 | ✅ | 执行准确率 runner 跑通；mock 驱动 10/10、首过率 100%、成本 ¥0 |
| LLM 选型评测脚本 | ✅ 就绪 | `eval/model_selection.py`（D1 决议格式：准确率→成本→延迟裁决）；**等 API key 即跑** |

### 关键决策（记录在案）

1. **Chinook 表/列名统一小写**：原 PascalCase 在 PG 无引号引用会被折叠，导致 LLM 生成的 SQL 报"表不存在"。DDL 生成器做 lower() 映射，偏离官方 schema 已在文件头注明。
2. **sqlglot 30.x `qualify(validate_qualify_columns=True)`** 做 AST 级列存在性校验（旧参数名已弃用）；CTE 表名白名单单独豁免。
3. **mock provider 贯穿全链路**：无 key 可开发/测试/演示（脚本化响应注入），真实评测只切 `.env`。
4. **容器以宿主 UID 运行**（compose `user:`）：避免挂载目录出现 root 属主文件。
5. **受限网络**：镜像统一 `DOCKER_REGISTRY_MIRROR` 前缀（.env 配 daocloud）；nginx 占位容器绕过 entrypoint（其 apk 探测在受限网络挂起）。

### 冒烟证据（quick_start.sh 输出）

- `GET /api/health` → `{"status":"ok","db":true,...}`
- 脚本化 eval：**execution accuracy 100% (10/10)，first-pass 100%，llm cost ¥0**
- 单测 27 passed（含 3 个真实 DB 集成用例：端到端/自修复恢复/别名改写）
- 别名改写：`"摇滚曲风有多少首歌" → "Rock 曲风有多少首歌"`（术语库链接，改写对照进 Trace）

### 待办（A · 本周内）

- [ ] API key 到位 → 跑 `eval/model_selection.py` 双模型 ×3 重复 → 报告 + 主力定案（D1，与 B 共同、C 主导用例）
- [ ] Trace 契约 B/C 评审 → 周五冻结（含 SSE 事件推送格式初稿）
- [ ] 多表用例集 20 个（`nl2sql_multi_table.jsonl`，Join 路径素材，喂 W3 #5）
- [ ] Prompt 三层模板静态层内容定稿候选（W2 冻结前的起草）

### B/C 协作提醒

- C：前端占位页在 `frontend/`（8090 端口），W2 直接替换；`/api/nl2sql` 返回结构含完整 trace 树（`trace.root.children[]`），DAG 可视化可以直接吃这个结构
- B：摄入中间表示契约（block/breadcrumb/formula 资产）待你起草后放 `docs/contracts/`
- 双方：`backend/app/core/llm.py` 的 `LLMService.chat()` 是唯一 LLM 入口，RAG 引擎请直接复用（缓存与记账自动生效）

## D2（9/22）· A 角色完成 ✅

### 交付物

| 项 | 状态 | 说明 |
|---|---|---|
| 多表用例集 20 个 | ✅ | `nl2sql_multi_table.jsonl`：2表×10 / 3表×5 / 4表×1 / 5表×1 / 自连接×1 / 嵌套子查询×3，覆盖 JOIN/GROUP/HAVING/NOT EXISTS/双 IN/派生表/日期；全部过预检（可执行、行数≤200、确定性、过校验器） |
| 用例预检脚本 | ✅ | `eval/preflight_cases.py`（C 后续加用例必过此门；行数预算 200 = 执行准确率对比的生命线） |
| D1 选型评测用例 | ✅ | `llm_selection_nl2sql.jsonl`（7 单表 + 3 JOIN，覆盖过滤/排序/聚合/LIKE/日期）；**RAG 5 例待 B 引擎就绪后补** |
| Prompt 静态层定稿候选 | ✅ | `docs/contracts/prompt_static_layer.md` + `prompts/agent.py`（意图分类模板候选）；含冻结纪律（只追加不改序 + 版本号失效缓存） |
| Trace SSE 事件格式初稿 | ✅ | trace.md §4.2：7 类事件（turn.start/trace.node/answer.delta/answer.done/clarify.request/turn.end/error），供 C 的 W2 前端开发 |
| 多表基线冒烟 | ✅ | 脚本化 mock 13/13（含 mt-001/002/017）——**W3 #5 Join 路径注入消融实验的基线数据点** |

### 用例设计过程中的数据事实（写入交接文档，避免 B/C 重复踩坑）

- Chinook master 版发票日期范围 **2021-01-01 ~ 2025-12-22**（不是网上教程里的 2013）
- **71 位艺术家无专辑**：问“每位艺术家专辑数”必须措辞“专辑最多的前 N 位”避免 JOIN/LEFT JOIN 歧义
- 本版 Chinook playlisttrack 稀疏但 Heavy Metal Classic 有 26 首（含专辑信息）
- 从未被购买的曲目 1519 首；Rock∩Jazz 双买客户 32 位（复杂嵌套用例的答案锚点）

## D2+（滚动更新）

- 9/23: （待更新）
