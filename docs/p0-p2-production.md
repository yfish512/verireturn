# P0–P2 可靠售后闭环

这版将售后 Agent 的语言层与业务事实分离。模型只能解析意图、回答已发布知识和调用受控工具；订单行金额、库存、支付、审核、权限和状态机都由 PostgreSQL 事务决定。

## P0：任务与订单行

`agent_threads.focus_task_id` 标识每条客户消息唯一可操作的焦点任务。一个会话可以保留多个 `agent_tasks`，客户可通过任务列表切换、恢复或归档；确认、取消和放弃产生的 Agent 控制消息也写入 `agent_message_records`，刷新或重启不会丢失。上下文投影只包含焦点任务和有限近期消息。

新售后必须用 `items[]` 选择订单行和数量；旧单商品订单仍自动兼容。领域服务在订单行锁下校验已退款量和已确认处理中量，金额来自服务端的订单行实付分摊，不能由调用方传入。

换货在客户确认时锁定库存行并建立 `inventory_reservations` 和 `exchange_fulfillments`。缺货写入运营事故而不创建虚假履约；取消时释放预占，替换发货时把预占变为实际扣减。

## P1：退款账务

退货仓签收会创建 `refund_intents` 和同事务 `OutboxEvent`。Worker 提交渠道退款，渠道回调必须满足原始 body HMAC、五分钟时间窗、事件头/正文一致、事件 ID 去重和退款金额一致。只有成功渠道事实会完成售后单并增加 `order_items.refunded_quantity`。

`POST /ops/payments/reconciliation` 运行渠道对账，结果区分匹配、渠道缺失和状态不一致；失败及差异写入运营事故。支付 webhook 允许 `PAYMENT_WEBHOOK_SECRET` 和 `PAYMENT_WEBHOOK_PREVIOUS_SECRET` 并行，方便无中断轮换。

## P2：身份、隐私与运维

生产环境要求 JWT，角色由服务器 `actors` 表决定，覆盖客户、客服/运营、审核、财务、运营主管和内部服务。应用按客户/IP 与接口类别限流；敏感密钥只从环境注入。客户可请求导出或删除，删除会脱敏客户对话和审核自由文本，账务与不可变审计保留以维持合规追溯。

`/health` 和 `/readyz` 用于存活与数据库就绪检查；Outbox 的租约、失败次数、死信和支付/库存异常都保留为可审计事实。CI 执行单元/API、PostgreSQL 迁移并发测试与前端构建。
