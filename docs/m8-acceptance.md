# M8 验收：可信会话记忆

| 条目 | 结果 | 证据 |
|---|---|---|
| 任务补槽 | 通过 | “退款、未拆封”创建 `create_after_sales` 任务并保留原始原因，要求订单号。 |
| 错号修复 | 通过 | `O10001` 返回订单不存在，任务回到 `collecting_slots`，不丢失退款意图。 |
| 恢复执行 | 通过 | 后续 `O1001` 恢复原退款路径，进入 `awaiting_customer_confirmation`。 |
| 确认后续 | 通过 | 确认退款后任务显式变为 `awaiting_pickup_slot`，取件时段不依赖旧 checkpoint 槽位。 |
| 线程隔离 | 通过 | `agent_threads.actor_id` 绑定客户；其他客户使用相同 thread ID 返回不存在。 |
| 重试幂等 | 通过 | `(thread_id, client_message_id)` 唯一；已处理消息返回保存的回复。 |
| 事件审计 | 通过 | `agent_task_events` 有顺序约束，并由 PostgreSQL trigger 禁止更新和删除。 |
| 前端恢复 | 通过 | `/agent` 通过线程快照恢复聊天记录和“当前任务记忆”卡片。 |
| 全量回归 | 通过 | `pytest -q` 为 54 passed、12 skipped；PostgreSQL 模式为 66 passed；`alembic check` 无 drift；前端生产构建通过。 |

真实 API 已验证三轮链路：“你帮我退一下商品吧，没拆封” → `O10001` → `O1001`。最后一轮创建待确认售后单；订单错误并未将任务退化为独立订单查询。
