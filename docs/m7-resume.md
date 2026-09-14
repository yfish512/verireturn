# M7 求职材料

## 简历项目描述

**VeriReturn｜可验证事务型电商售后 Agent** ｜ Python、FastAPI、LangGraph、PostgreSQL/pgvector、React

- 设计并实现中文电商售后 Agent：以 LangGraph 编排受控订单、资格、售后和履约工具；将退款/换货等写操作置于持久化确认 interrupt 后，避免模型直接修改业务事实。
- 构建 PostgreSQL 事务链路：以幂等键、乐观锁、append-only 审计、Transactional Outbox/Inbox 和 HMAC Webhook 支持审核、取件、退款/换货的可恢复异步履约。
- 实现版本化 RAG、人工审核与运营治理：pgvector 混合检索按客户/运营受众隔离，M6 将 Agent 工具、审核、履约投影为 Trace，并生成指标、去重告警和 checksum 固定的评测结果。
- 完成 61 项 PostgreSQL 集成回归、50 项单元/API 回归；M7 提供非 Docker 一键演示和数据库验收器，覆盖确认、履约、审核、Trace 与告警闭环。

## 面试讲述主线

业务问题是售后场景的模型回复不能直接成为退款事实。我的处理方式是将模型限制为意图解析、知识解释和工具编排；订单资格、金额、权限、审核和状态转移全部由后端领域服务和 PostgreSQL 决定。高风险工具调用先落库为待确认命令，确认后从 LangGraph checkpoint 恢复。履约不信任“工具调用成功”，而是等待验签 Provider 回调写入 Inbox，再产生不可变履约事件。最后用 Trace 和评测把每个结论连接回真实业务记录。

## 高频追问

| 问题 | 回答要点 |
|---|---|
| 为什么不用 Agent 直接调用数据库？ | 数据库权限、资格与金额判断应有确定性边界；Agent 只能调用 typed HTTP 工具。 |
| 为什么需要确认 interrupt？ | 创建售后是写操作；确认前只有 pending case，checkpoint 让进程重启后仍能恢复同一用户意图。 |
| 为什么同时有 Outbox 和 Inbox？ | Outbox 保证本地业务意图与投递同事务提交；Inbox 用 Provider event ID 去重，只有验签外部事实才能推进履约状态。 |
| RAG 会不会决定是否退款？ | 不会。RAG 只检索已发布文档并返回引用；资格和金额始终由订单事实与领域规则计算。 |
| 如何证明项目不是 Demo 聊天机器人？ | M7 verifier 直接验证确认、工具 trace、审核事件、HMAC 履约事件、Outbox 状态、指标快照和告警生命周期。 |
