# M1 验收：可验证售后事务工具后端

M1 不接入 LLM、LangGraph 或聊天 UI。它交付 Agent 可调用的业务执行底座：模型只能提出受约束的工具请求，资格、权限、状态变化、幂等和审计由服务与数据库决定。

## 验收流程

```sh
mamba run -n verireturn sh scripts/local_postgres.sh start
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  mamba run -n verireturn alembic upgrade head
mamba run -n verireturn pytest -q
RUN_POSTGRES_INTEGRATION=1 \
  mamba run -n verireturn pytest -q backend/tests/test_postgres_integration.py
```

最后一条测试会创建、迁移并删除独立的 `verireturn_m1_test` 数据库；它不会写入演示数据库。

## 必须满足的业务不变量

| 不变量 | 验证方式 |
|---|---|
| 只有订单所属用户可查询和操作订单、售后单 | API 与服务层越权测试返回 `ORDER_ACCESS_DENIED` 或 `CASE_ACCESS_DENIED` |
| 退款、换货资格由确定性规则决定 | 单测覆盖 7/8 天、30/31 天、未签收、拆封、质量问题和未知类型 |
| 高风险操作需要显式确认 | 仅 `pending_confirmation → awaiting_pickup` 后可预约；否则返回 `CASE_NOT_CONFIRMED` |
| 状态不能跳转或回退 | 状态机仅允许已定义边；终态再次确认、完成等操作返回 `INVALID_STATE_TRANSITION` |
| 每个写命令可重试 | `Idempotency-Key` 是所有写 API 的必填 header；同 key、同请求返回既有结果 |
| 同一幂等键不可承载不同请求 | 请求规范化 SHA-256 指纹不一致时返回 `IDEMPOTENCY_KEY_CONFLICT` |
| 并发重试不重复执行 | PostgreSQL 双 session 测试断言只有一张售后单和一条 `CASE_CREATED` 审计事件 |
| 状态与审计同步提交 | 每个状态迁移在同一事务写入 case、audit log、idempotency record |
| 每个关键变化可追溯 | 可查询按 ID 排序的审计事件，包含 actor、request ID、时间和业务说明；PostgreSQL trigger 拒绝更新或删除审计行 |

## 状态机

```text
pending_confirmation ── 用户确认 ──> awaiting_pickup ── 预约取件 ──> pickup_scheduled
         │                                      │                                  │
         └──────── 用户取消 ───────> cancelled  └──────── 用户取消 ────> cancelled  │
                                                                                物流回调
                                                                                     │
                                                                                 completed

pending_confirmation / awaiting_pickup / pickup_scheduled ── 系统异常 ──> manual_review
```

`completed`、`cancelled` 和 `manual_review` 都是 M1 终态。转人工接口要求 `X-Internal-Service-Key`。M5 后，旧的内部完成接口还必须显式设定 `LEGACY_FULFILLMENT_SIMULATOR_ENABLED=true`，只用于回放 M1 本地演示；正常履约完成只能由 M5 的已验签 Provider Webhook 推进。

## 身份与工具边界

开发 API 使用 `X-Demo-User-Id` 作为网关已认证身份的替身。请求 body 不含 `user_id`，且 Pydantic 拒绝额外字段；生产版替换该 dependency 为网关/JWT 提供的 `ActorContext`，而非让 Agent 或用户自由指定身份。

## M1 演示脚本

以 `U001` 和 `O1001` 为例：查询订单、资格检查、创建退款单、用户确认、预约取件、内部物流完成，最后查询审计记录。应出现：

```text
CASE_CREATED → CASE_CONFIRMED → PICKUP_SCHEDULED → CASE_COMPLETED
```

并额外演示：越权订单、拆封退款、未确认预约、同幂等 key 重试、同 key 不同原因和并发创建。
