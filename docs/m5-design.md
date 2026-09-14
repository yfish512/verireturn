# M5 设计：事件驱动的售后履约与主动服务闭环

M1–M4 解决了售后申请、受控 Agent、人工审核和知识问答。M5 处理另一个不可由同步 HTTP 假设掩盖的问题：预约取件之后，物流和支付侧的事实会迟到、重试、重复及乱序到达。M5 将履约事实与对外投递建模为可恢复的 PostgreSQL 事务流程。

## 边界与目标

- 客户预约取件时，在同一数据库事务中写入售后状态、审计记录与 `pickup.requested` Outbox 事件。
- Worker 用 `FOR UPDATE SKIP LOCKED`、租约、指数退避和投递流水处理 Outbox。语义是**至少一次投递**；第三方幂等键和本地 Inbox 去重共同保证一次业务效果。不会错误宣称跨网络的 exactly-once。
- Provider Webhook 先验证 HMAC-SHA256、五分钟时间窗、事件 ID 和载荷摘要，才进入 Inbox。重复事件只返回既有结果；同一 ID 的不同载荷被拒绝。
- 唯一可信的履约状态推进来自已验签、按序可应用的 Provider 事件。Agent 与客户查询接口只读这些事实，不能调用内部回调、改写物流/退款状态或声称未发生的结果。
- 缺少前序事件的回调保留为 `deferred`；运营可确认、重放或解决事故。状态不匹配、序列冲突和重试耗尽会创建可追踪的事故单。

## 状态与事件契约

退款：`pickup_scheduled → picked_up → return_received → refund_processing → completed`。

换货：`pickup_scheduled → picked_up → return_received → replacement_shipped → completed`。

Provider 事件允许值为 `pickup.collected`、`return.received`、`refund.processing`、`refund.completed`、`replacement.shipped`、`replacement.delivered`。每个事件包含 `event_id`、`case_id`、`event_type`、正整数 `sequence_no`、UTC `occurred_at` 和 JSON `payload`。同一个 `(provider, case_id)` 序列严格递增；下一个序号以外的有效事件不会跳过状态而是暂存。

业务事件 `fulfillment_events` 是已被实际应用的不可变事实；Inbox 保存所有验签过的收到事件及其处理状态；Outbox 保存本地事务已承诺但尚未成功交付的意图；`outbox_deliveries` 是每次尝试的审计记录。客户通知也先作为 Outbox 意图，再由 Worker 标记为已发送或失败。

## 安全、可恢复性与运营

Webhook 的签名输入为原始请求体，header 为 `X-Provider-Signature: sha256=<hex>` 和 `X-Provider-Timestamp`。Provider 身份、事件 ID、序号和摘要均被持久化。回调密钥只读环境变量 `FULFILLMENT_WEBHOOK_SECRET`，不进入日志或数据库。

Worker 声明事件后短暂提交租约；成功、失败、过期租约均可恢复。演示 Provider 是进程内适配器，确定性地确认预约请求，目的在于验证适配器、重试与幂等键契约，不能被描述为真实物流集成。生产适配器可替换为 HTTP Provider，仍使用相同 `provider_idempotency_key`。

运营端提供事故列表、领取、重放延迟 Inbox 事件和解决操作。所有动作有乐观版本检查和 `fulfillment_events`/审计证据。M5 不引入 Kafka、Redis、Celery 或多 Agent：在单库规模下 PostgreSQL 锁、Outbox 和 Inbox 足以展示可靠履约基础；吞吐、独立消费组或跨服务扇出成为实际需求时再演进。

## 验收矩阵

| 验收项 | 证据 |
|---|---|
| 预约与 Outbox 原子性 | 同一事务内有 `PICKUP_SCHEDULED` 审计和一条 `pickup.requested`，回滚时两者均不存在 |
| Worker 并发与恢复 | PostgreSQL 两 Worker 只领取一次；过期 lease 可重新领取；失败有投递流水与退避 |
| 回调安全 | 错签名、过期时间、body/header 事件 ID 不一致被拒绝且不写履约状态 |
| 重复/乱序 | 相同回调不会新增业务事实；同 ID 不同载荷冲突；乱序为 `deferred`，待前序到达后可重放 |
| 事务状态机 | 退款、换货的完整合法链路通过；错误类型或跳步产生事故、不推进售后状态 |
| 主动服务与 Agent 边界 | 状态推进会生成通知意图；通知投递可重试；Agent 工具只有只读履约查询 |
| 交付 | SQLite 确定性测试、PostgreSQL 集成测试、Alembic check、前端构建及真实模型回归 |
