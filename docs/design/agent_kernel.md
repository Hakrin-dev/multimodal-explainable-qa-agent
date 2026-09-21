# Agent 编排内核设计 v1（A · D3 起草，W2 冻结）

> 实现：`backend/app/agent/`（kernel / intent / planner / tools / memory）
> 关联：PLAN §4.1（内核）、§4.5（跨源）、§4.6（多轮）、§4.7（澄清）；
> 事件契约 `docs/contracts/trace.md` §4.2

## 1. 设计原则（对应架构三原则）

1. **事件驱动 + 观察者**：内核只负责编排，所有过程产出通过 `on_event` 回调外发
   （SSE 转发 / JSON 忽略 / 测试断言三种消费形态共用一条代码路径）
2. **工具即流水线**：nl2sql / rag_search 工具直接包装既有 Pipeline，
   Trace 天然嵌套（tool_call 节点下挂 rewrite/sql_gen/… 子树）——两流水线同构是 W1 刻意铺垫
3. **诚实降级**：工具失败 → `degraded` 状态 + 明确原因（"知识库未就绪"），
   绝不编造；跨源 fuse 对缺失子任务如实说明
4. **知识外置**：意图路由的关键词兜底、工具清单、澄清槽位全部外置可扩展

## 2. 一次 Turn 的执行流

```
question ──► ① intent（LLM 结构化输出，history-aware，JSON 解析失败→重试→关键词兜底）
         ──► ② 路由
   CHAT      → 直接生成
   DB_QUERY  → nl2sql 工具（history 透传给改写/生成——澄清恢复的关键）
   DOC_QUERY → rag_search 工具
   HYBRID    → ③ plan（LLM 分解子任务链）→ 顺序执行（{上一步结果} 占位回填）
              → ④ fuse（分区溯源生成：[数据库]/[文档名+页码]）
   AMBIGUOUS → ⑤ clarify：挂起（SessionStore.pending_clarify）+ clarify.request 事件
              → 下轮用户补充 → history 驱动意图完整化 → 正常执行（无需专门回填代码）
         ──► ⑥ 记忆簿记（user/assistant 入历史；澄清状态挂起/清除）
         ──► turn.end（时延 + 本轮成本归因，从 Trace LLM 节点聚合）
```

## 3. 模块职责

| 模块 | 职责 | 扩展点 |
|---|---|---|
| `intent.py` | 一次 LLM 调用分类 5 类意图 + 槽位 | 槽位矩阵外置（W4 §4.7 完整化） |
| `planner.py` | HYBRID 子任务分解 + fuse 生成 | v1 线性链 → W4 真依赖 DAG（并行分支 + fan-in） |
| `tools.py` | ToolRegistry（注册/清单/调度） | `formula_eval` W4 接入；新工具 = 一个 ToolSpec |
| `memory.py` | 会话历史 + 澄清挂起（RLock 线程安全） | W2 落 PG（接口不变：messages in/out） |
| `kernel.py` | 路由/编排/事件/成本归因 | — |

## 4. 已验证的端到端场景（deepseek-chat 实测）

| 场景 | 结果 | Trace 形态 |
|---|---|---|
| 闲聊 | CHAT → 直接回答 | intent → chat_generate |
| 问数 | DB_QUERY → 工具 | intent → nl2sql → (rewrite→…→summarize) |
| 澄清挂起/恢复 | "增长率"→挂起(缺时间范围)→"对比 2024 和 2023"→**正确算出 1.69%** | 两轮 trace，恢复轮为完整 DB_QUERY |
| 跨源多跳 | "2024 销售冠军+方法论"→ plan→nl2sql(冠军)→rag(降级)→fuse 分区溯源 | intent→plan→subtask×2→fuse（DAG 形态） |
| SSE 流 | turn.start→trace.node×N→answer.delta→answer.done→turn.end | 契约顺序 |

## 5. 关键决策记录

1. **澄清恢复走 history 而非专门回填代码**：澄清问答入会话历史，下轮意图分类
   基于历史产出完整意图——零专门状态机代码，天然支持多轮澄清（W4 槽位矩阵是其增强而非替换）
2. **SSE 的 answer.delta v1 为整段推送**（内核先完成再推）：真流式（LLM stream→delta）
   在 W2 与 C 的前端联调时加，事件契约不变
3. **needs_clarification 上浮**：流水线内模型拒答（缺条件）→ 内核转 honest 降级而非"未返回数据"
4. **成本归因从 Trace 聚合**（llm_call 节点 detail.cost_rmb 求和）→ 每轮可查成本，账本 SQLite 是全局视图
5. **死锁教训**：SessionStore 方法间嵌套调用必须 RLock（W1-D3 踩坑，测试用 faulthandler 定位）

## 6. W2 待办（在 v1 骨架上增量）

- [ ] 会话/Trace 落 PG（`trace_event` 表 = flat_events 形态，`session` 表）
- [ ] LLM 真流式 → answer.delta 逐段推送（前端联调时）
- [ ] 槽位矩阵外置配置（`data/` 下 YAML：问题类型→必要槽位→候选选项）
- [ ] HYBRID 真 DAG（并行子任务 + 依赖编排）
- [ ] 澄清多轮（>1 次追问）与澄清超时清理
