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

## D2+（滚动更新）

- 9/29: （待更新）
