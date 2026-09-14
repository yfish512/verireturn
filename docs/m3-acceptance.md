# M3 验收：售后运营闭环

验收时间：2026-09-14。M3 在 M1 的事务状态机和 M2 的结构化确认边界上增加人工审核，不改变既有 `manual_review` 终态。

## 已交付

- 服务端 `ActorContext`：角色由数据库映射，客户端 Header 只能标识 actor，不能提交或伪造角色。
- JWT 身份适配器：`APP_ENV=production` 时服务拒绝 demo Header 模式；JWT 只提供经签名验证的主体 ID，角色始终由服务端 `actors` 表映射。
- `policy_versions`：运营主管原子发布新版本，既有 published 版本只退役；工单和审核通过后创建的售后单固定关联策略版本。
- `review_tickets` 与 append-only `review_events`：客户提交、领取、补料、拒绝、批准例外和关闭重复请求均写结构化事件。
- 审核批准在一个事务中创建关联的 `pending_confirmation` 替代售后单；客户仍须使用 M1 确认接口。
- LangGraph 审核提交确认：Agent 先持久化确认草稿，只有客户批准后才调用审核工单 API。
- 运营 API：审核队列、时间线、领取、决策、策略版本和指标摘要。
- React/TypeScript 运营工作台：队列、指标、领取、时间线和结构化决策表单。

## 数据库不变量

1. 原 `manual_review` 售后单不会被重新打开或改写；审核批准只创建新的关联售后单。
2. `review_tickets.version` 和 `SELECT ... FOR UPDATE` 防止并发领取、决策覆盖。
3. 所有客户、运营和策略发布写命令要求 `Idempotency-Key`；相同 key 的不同请求返回冲突。
4. `review_events` 与既有 `audit_logs` 都由 PostgreSQL trigger 强制 append-only。
5. `after_sales_cases.source_review_ticket_id` 是唯一外键，一张审核工单至多创建一张替代售后单。

## 验收命令与结果

```sh
RUN_POSTGRES_INTEGRATION=1 \
  /data/user004/miniforge3/bin/mamba run -n verireturn pytest -q
# 34 passed, 1 warning

DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  /data/user004/miniforge3/bin/mamba run -n verireturn alembic check
# No new upgrade operations detected.

cd frontend && npm run build
# TypeScript + Vite production build succeeded.
```

PostgreSQL 集成测试覆盖 M1 并发幂等、审计不可变、M2 checkpoint 恢复，以及 M3 两个运营人员并发领取仅一个成功、审核事件不可篡改、并发策略发布始终只保留一个 published 版本和策略内容不可篡改。M3 单元/API 测试覆盖 JWT 主体优先于伪造 Header、生产环境拒绝 demo 身份、客户越权、伪造 role Header、版本冲突、补料归属、策略发布权限、审核批准的幂等和客户二次确认。

## 真实模型闭环

使用本地 `.env` 的真实 DeepSeek OpenAI-compatible 配置验证：质量争议退款文本被解析为 `request_manual_review`；确认前未创建审核工单，客户结构化确认后创建工单。运营领取并批准后，系统创建新的 `pending_confirmation` 售后单；客户确认后进入 `awaiting_pickup`。审核时间线和售后审计均可查询。

M3 改动后于 2026-09-14 复跑 M2 真实 DeepSeek 30 case 回归，结果为 30/30 通过，累计 69.234 秒；报告保存在被 Git 忽略的 `evals/reports/m2-deepseek-post-m3-fixed.json`。M3 另有独立真实 DeepSeek runner `evals/run_m3_evals.py`：5/5 通过、累计 6.788 秒，覆盖确认前零工具调用、拒绝、重复消息、跨用户确认、运营批准和客户二次确认。报告为 `evals/reports/m3-deepseek-accepted.json`。M3 的确定性状态、权限和事务测试不依赖模型采样。

## 运行运营工作台

先启动后端，再启动 Vite：

```sh
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  /data/user004/miniforge3/bin/mamba run -n verireturn uvicorn backend.app.main:app --port 8000

cd frontend
npm run dev
```

开发演示运营身份为 `OPS001`，运营主管为 `OPS_MANAGER`。前端只允许切换当前 actor ID；后端从 `actors` 表读取角色，因而不能通过前端或 Header 的 role 字段提升权限。生产环境设定 `APP_ENV=production`、`AUTH_MODE=jwt`、`AUTH_JWT_SECRET`（至少 32 字符），并可选配置 issuer/audience；此时服务不再接受 `X-Demo-User-Id`。

## 向量数据库结论

M3 未加入向量数据库。审核、策略、权限和状态均为结构化事务事实，PostgreSQL 是唯一真相源。未来仅在有稳定 SOP 文档语料和检索痛点时，再以 PostgreSQL `pgvector` 做带引用的辅助检索；它不能参与资格、金额、权限、状态或审核决策。
