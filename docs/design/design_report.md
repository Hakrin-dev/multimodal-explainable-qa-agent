# 多模态可解释问答智能体设计报告（骨架）

> 版本：v0.1（W3 骨架，2026-10-03）  
> 目标篇幅：≤20 页。本文先固定章节和证据入口，正式提交前补充截图、演示结果与引用。

## 0. 核心亮点预览页（1 页）

**一句话**：问迹将结构化问数、文档检索和跨源推理统一到事件驱动 Agent 内核，以 Trace、引用和可复现评测让每一步可解释。

**三项可量化亮点**

| 亮点 | 当前证据 | 设计价值 |
|---|---|---|
| 检索式要素中台 | Schema Linking v1 平均表数 7.5→2.1，schema tokens 约 -74% | 降低提示成本并减少干扰表 |
| 真依赖 DAG | HYBRID 独立子任务并行，Trace 保留父子关系 | 支持跨库/跨文档组合回答 |
| 成本与质量可审计 | L0/L1/L2、缓存账本、引用与 factcheck Trace | 结果可复现，预算可控 |

## 1. 背景、问题与目标

### 1.1 业务痛点

- 业务人员的问题同时涉及数据库指标和制度文档；单一 SQL 或单一 RAG 难以覆盖。
- 黑盒答案无法核验 SQL、数据来源、推理过程和成本。

### 1.2 目标与范围

- 范围：Chinook 结构化数据、知识库 PDF、NL2SQL/RAG/HYBRID、多轮澄清。
- 目标：执行准确、引用可定位、Trace 可回放、评测和费用可复现。
- 非目标：本阶段不承诺通用 OCR 字符识别和任意数据库自动迁移。

## 2. 总体架构

> 插入架构图：`docs/design/architecture.png`（待补图）。

1. Vue 3 前端通过 `POST /api/chat/stream` 消费 SSE。
2. Agent Kernel 负责意图识别、计划、依赖 DAG、工具调度和结果融合。
3. NL2SQL 走改写、Schema Linking、SQL 生成/校验/执行/修复；RAG 走摄入、混合检索、引用生成和忠实度检查。
4. TraceCollector 统一记录节点树，`GET /api/trace/{turn_id}` 支持断线回放。

## 3. 核心流程设计

### 3.1 DB_QUERY

问题 → 术语链接 → 压缩 Schema → SQL 生成 → sqlglot 校验 → 只读执行 → 自修复 → 结果总结。

### 3.2 DOC_QUERY

问题 → 混合检索 → Top-K 片段 → 带 Citation 的回答 → factcheck（可选）。

### 3.3 HYBRID 与多轮

计划节点声明 `depends_on`，独立子任务并行执行，融合节点消费前置结果；澄清通过 slot matrix 决定是否挂起并在同一 `session_id` 恢复。

## 4. 可解释性与安全边界

- Trace 契约见 [`docs/contracts/trace.md`](../contracts/trace.md)，SSE 字段和事件顺序冻结。
- Citation 只承诺 PDF 页级定位，不虚构页内坐标。
- 数据库查询使用只读连接和超时；文档 PDF 端点防路径穿越。
- 前端流式请求采用首事件超时，首事件到达后由用户手动停止。

## 5. 评测与成本工程

- L0：每类 5 个 smoke；L1：全量回归；L2：主备模型 × 3 重复。
- 入口：`backend/eval/run_levels.py`；明细和 manifest 写入 `backend/var/eval/`。
- 用量：`GET /api/usage` 与 `backend/eval/usage_report.py`；记录 tokens、缓存命中、费用和延迟。
- W3 已验证：NL2SQL 31/31、RAG 8/8、多轮 14/14；真实模型结果以对应报告为准。

## 6. 风险、边界与后续

- 用例集仍需扩充到约 140+；多文档、鲁棒性和澄清集列入后续增量。
- 文档管理台依赖上传/质量/修复 API，前端接入属于 W4。
- 真实模型和 GPU 评测必须记录 provider、模型、环境及成本，禁止用 mock 结果替代。

## 7. 附录清单（待补）

- [ ] 核心场景截图与 10 场景覆盖矩阵
- [ ] L1/L2 趋势图和成本曲线
- [ ] Trace/DAG 与 Citation UI 截图
- [ ] 术语、Schema Linking 和 RAG 消融数据表
