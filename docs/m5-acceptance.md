# M5 验收：事件驱动售后履约

验收时间：2026-09-14。M5 的通过依据是数据库事实、投递记录、签名验证和 Agent tool trace，不以自然语言回复作为通过依据。

| 项目 | 结果 | 证据 |
|---|---|---|
| 预约与 Outbox 原子性 | 通过 | `schedule_pickup` 在既有状态变更事务内写入 `pickup.requested`；确定性测试验证事件存在、Worker 只将其投递为成功记录。 |
| Worker 并发、租约与重试 | 通过 | PostgreSQL 集成测试让两个 session 同时 `SKIP LOCKED` 领取同一 Outbox，仅一方成功；失败投递产生 `outbox_deliveries`，退避后可恢复。 |
| Provider 回调安全 | 通过 | API 测试验证 raw-body HMAC、时间戳与 header/body 事件 ID；错误签名返回 403 且不产生履约状态变更。 |
| 重复与乱序 | 通过 | 相同 event ID 与相同摘要返回原 Inbox 结果，不新增 `fulfillment_events`；序号缺口标记 `deferred` 并创建事故，运营重放后按顺序应用。 |
| 履约状态机 | 通过 | 单元测试覆盖退款完整链路、换货上收到退款事件的拒绝，以及完成状态和客户通知意图。 |
| 运营闭环 | 通过 | 事故领取/解决使用乐观版本；两个操作均写入不可变 `audit_logs`。运营台可查看、领取与解决 M5 履约事故。 |
| Agent 边界 | 通过 | Agent “售后单 #42 进度”只调用 `get_fulfillment_status`；M5 evaluator 在已签名的履约事实前后比较 `fulfillment_events`，确认零写入。 |
| 迁移与构建 | 通过 | 主 PostgreSQL 已迁移到 `20260914_0014`，`alembic check` 为 `No new upgrade operations detected.`；前端生产构建成功。 |

实际验证：

```text
pytest -q
44 passed, 8 skipped, 1 warning

RUN_POSTGRES_INTEGRATION=1 pytest -q
52 passed, 1 warning

alembic check
No new upgrade operations detected.

frontend/npm run build
TypeScript + Vite production build succeeded.

run_m5_evals.py（本地 keyword fallback）
1/1 passed，1.775 s
```

真实 DeepSeek M5 评测已于本次审计完成：`evals/run_m5_evals.py` 为 1/1 通过，校验已验签履约事实前后 `fulfillment_events` 数量不变，且 Agent trace 只有 `get_fulfillment_status`。报告为被忽略的 `evals/reports/m5-deepseek-accepted.json`。本次同时重跑既有 runner：M2 30/30、M3 5/5、M4 4/4，报告分别为 `m2-deepseek-post-m5.json`、`m3-deepseek-post-m5.json`、`m4-deepseek-post-m5.json`。

前端仍提示 Ant Design 使初始 JavaScript 约 1.15 MB；这不是 M5 功能或一致性阻塞项。
