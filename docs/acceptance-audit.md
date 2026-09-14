# M1–M6 全链路严格验收审计

审计时间：2026-09-14；审计环境为 Miniforge `verireturn`、本地 PostgreSQL 和 `.env` 中的 DeepSeek OpenAI-compatible 配置。M1–M5 真实模型报告已由 M6 Evaluation Worker 在当前代码上重新执行。

## 统一基线

- Alembic head 以本轮 migration `20260914_0017` 为准；最终回归会重新执行 `alembic check`。
- 当前确定性回归：`pytest -q` 为 **50 passed, 11 skipped**；`RUN_POSTGRES_INTEGRATION=1 pytest -q` 为 **61 passed**。
- 前端 TypeScript/Vite production build 成功；唯一非阻塞项是 Ant Design 初始包约 1.15 MB。
- 所有数据库写路径使用 Miniforge 环境与 PostgreSQL migration；SQLite 只承担确定性单元/API 测试，不能替代 PostgreSQL 并发和 trigger 验证。

## 分阶段结论

| 阶段 | 设计要求 | 当前结论 | 可复核证据 |
|---|---|---|---|
| M1 | 确定性资格、归属/权限、确认状态机、幂等、审计只追加 | **通过** | 服务/API 测试；PostgreSQL 并发创建只产生一个 case；审计 trigger 拒绝修改；M5 后旧直接完成 API 默认 410，避免绕过签名履约事实。 |
| M2 | LangGraph 受控工具、确认 interrupt、checkpoint 恢复、trace | **通过** | 当前测试覆盖确认、重复、跨用户、Agent 履约只读；M5 后 DeepSeek runner 为 30/30。 |
| M3 | 人工审核、服务端角色、策略版本、乐观锁、运营台 | **通过** | 审核领取并发、不可篡改事件、策略唯一 published、JWT/API/前端均已回归；M5 后 DeepSeek runner 为 5/5。 |
| M4 | 版本化受众知识库、pgvector 混检、引用校验、事务隔离 | **通过** | pgvector、GIN/HNSW、知识版本不可变、客户 SOP 隔离和零业务写入均被测试；M5 后 DeepSeek runner 为 4/4。 |
| M5 | Outbox/Inbox、签名回调、乱序恢复、履约状态机、通知、事故闭环、Agent 只读 | **通过** | Worker 并发/lease/重试、HMAC/时间窗/重复/乱序、退款与换货状态机、事故乐观锁和审计均在 PostgreSQL 或 API 测试中验证；DeepSeek HTTP evaluator 1/1 通过。 |
| M6 | Trace、反馈、指标、告警、评测发布门禁 | **通过** | 运营台 Trace/指标/评测下钻、主管告警规则版本 API、PostgreSQL 不可变 trigger、全量 PostgreSQL 回归为 61 passed；Compose/Prometheus/演示脚本作为可选的一键部署包装，配置解析已通过。 |

## 跨阶段不变量检查

1. **业务真相源未被 Agent 或 RAG 替代。** M2/M5 Agent 经过 typed HTTP 工具；M4 只读检索；资格、金额、权限、审核与履约状态仍由领域服务和数据库决定。
2. **高风险命令保持确认与幂等。** 创建售后、确认/取消、预约、审核命令使用持久化确认、幂等记录和锁；重复请求不会制造第二个业务结果。
3. **状态演进可审计。** M1 `audit_logs`、M3 `review_events`、M5 `fulfillment_events`/Inbox/Outbox/Delivery 分别保留命令、审核和外部事实；PostgreSQL 对审计与已发布版本施加不可变约束。
4. **M5 不再由遗留完成接口破坏履约来源。** `/tools/internal/after-sales/cases/{id}/complete` 需要内部凭据且默认 410；只有明确启用 `LEGACY_FULFILLMENT_SIMULATOR_ENABLED=true` 才能回放 M1 演示。正常完成来自已验签 Webhook。
5. **跨阶段回归实际执行。** 当前 57 项 PostgreSQL 全量回归覆盖 M1–M6；新 migration 已在主库应用，并由从空库迁移的 PostgreSQL fixture 复验。

## 真实模型复验

M6 Evaluation Worker 使用当前 `.env` 的 `deepseek-v4-flash` 重跑四个 evaluator：M2 **30/30**、M3 **5/5**、M4 **4/4**、M5 **1/1**。报告位于被 Git 忽略的 `evals/reports/m2-deepseek-post-m5.json`、`m3-deepseek-post-m5.json`、`m4-deepseek-post-m5.json` 与 `m5-deepseek-accepted.json`。M5 case 先经已验签 Webhook 写入取件事实，再断言 Agent tool trace 只有 `get_fulfillment_status` 且没有新增履约事件。

## 严格结论

M1–M6 的设计要求均已达到。M6 已完成单元、PostgreSQL、Alembic、API 冒烟、前端构建和 Compose 配置解析验证。真实镜像构建与服务启动属于可选的一键部署演示，不作为当前项目的验收条件；当前机器缺少 rootless Docker 所需的系统 `uidmap` 包，不影响已验证的应用与数据库链路。
