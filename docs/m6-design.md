# M6：Agent 质量治理与运营可观测性

M6 将 M1–M5 已持久化的业务事实投影为可追踪链路、滚动指标、告警和版本化评测。它是只读治理层：不会自动改变订单、售后单、策略、知识、审核或履约事实。

## 业务链

`AgentRun → AgentToolCall → AuditLog/Case → Review/Knowledge/Fulfillment` 由 `run_id`、`m1_request_id`、`case_id` 和外部事件 ID 关联。客户可评价自己的 Agent Run；指标 Worker 使用 PostgreSQL lease 聚合窗口快照；已发布告警规则读取快照并创建去重告警；运营人员领取和解决告警；评测任务由独立 Worker 调用既有 evidence-first evaluator 并将逐 case 结果持久化。

## 数据与不变量

- `agent_run_feedback` 以 `(run_id, actor_id)` 唯一，客户只能评价自己的 Run。
- `metric_computation_jobs` 以时间窗口唯一；Worker 用 `FOR UPDATE SKIP LOCKED` 和 lease 防止并发重复计算。
- `metric_snapshots` 以指标、窗口、维度唯一，只保存聚合数字，不保存客户原文。
- `ops_alerts` 以规则版本和 fingerprint 唯一；`ops_alert_events` 和 `evaluation_results` 由 PostgreSQL trigger 强制只追加。
- `evaluation_suites` 固定 case 文件 checksum；Worker 在执行前校验 checksum、绑定 Run 记录的模型名，并只接受已登记的提示词版本。`evaluation_runs/results` 固定模型、提示词版本和每条证据。
- Alert、Metric、Evaluation 表不被 M1–M5 的事务服务读取，因此不能反向影响业务结论。

## 技术取舍

FastAPI、SQLAlchemy、Alembic 与 PostgreSQL 继续作为主栈。Worker 继续使用 PostgreSQL 锁和租约；Prometheus Client 保留给运行时 scrape 指标，PostgreSQL snapshot 负责运营台历史查询。当前不引入 Kafka、Redis、Celery 或时序数据库，因为单库可靠任务、指标窗口和演示规模足够；未来达到多消费者、高吞吐或长期高基数指标时再拆分。

## 指标与规则

当前内置 `agent.runs_total`、完成率、工具成功率、负反馈率、审核积压、Outbox 积压和开放履约事故数。种子规则对 Outbox 积压、开放履约事故和负反馈率产生 warning/critical 告警。运营台展示概览、运营告警和既有 M5 事故；API 提供 Trace 下钻、窗口任务和评测 Run 队列。

## 验收

1. 反馈有归属和一次性约束，且不改变 Agent Run。
2. 指标 Job 可重试、并发领取安全，窗口生成七类快照。
3. 告警按 fingerprint 去重，领取/解决受乐观锁保护并产生 append-only 事件。
4. Trace 能从 Agent tool result 关联到售后单和 M1 审计。
5. 评测 Run 固定 suite checksum，独立 Worker 执行已有真实模型 evaluator 并保存逐 case 结果。
6. M1–M5 PostgreSQL、前端和真实模型回归不退化。
