# M10：可靠售后生产化边界

M10 将售后写操作按事实来源划分。客户和 Agent 只创建带幂等键的意图；订单行、退款额度、库存、审核结论和支付到账由领域服务和已验签 Provider 事件决定。Agent 的数据库记忆只保存焦点任务和有限脱敏对话，不能覆盖支付、履约、库存或审核事实。

## 资金、库存与逆向履约

退款回调必须带事件 ID、金额、币种和发生时间；同一事件的载荷哈希不一致会拒绝。`refund.completed` 不再是履约 webhook 事件，只有支付结算能完成退款。订单行在 `SELECT FOR UPDATE` 下验证可处理数量，退款成功才累加 `refunded_quantity`。换货库存预占有过期时间；维护 Worker 释放过期预占并创建可运营的履约异常。

取件预约被解析为上海时区的 UTC 起止窗口，在容量行上加锁后预留；取消同步释放容量。客户界面展示可用窗口。多商品订单必须选择订单行和数量，前端把选择转为受控行 ID，服务端仍校验归属、数量与金额。

## 审核与附件

附件先通过 `POST /review-tickets/uploads` 上传。开发存储适配器把不超过 5 MB 的 JPEG、PNG 或 PDF 写进 `REVIEW_UPLOAD_DIR`，数据库记录所有者、SHA-256、MIME 和受控引用。绑定审核单时再验证这些事实，任意手填 object reference、跨客户引用、哈希或 MIME 不一致都会被拒绝。生产部署可把该适配器替换为 S3/MinIO 预签名上传，并在完成回调中写相同元数据。

`review-sla` Worker 将超过 `due_at` 的开放、已领取或待补件工单原子投影为 `expired`，并追加 `REVIEW_SLA_EXPIRED` 审计事件。它是幂等的，可在崩溃后重复执行。

## 身份、隐私和运行

生产设置 `APP_ENV=production` 与 `AUTH_MODE=jwt`。可配置共享密钥 HS256，或配置 `AUTH_JWKS_URL` 使用带 `kid` 的 RS256/ES256 OIDC JWKS。Token 中的 role 从不参与授权，角色始终来自 `actors`。

设置 `REDIS_URL` 后限流器使用 Redis sorted set 的原子滑动窗口；生产 Redis 不可用时返回 503，开发环境回退到进程内限流。对话投给模型前会移除手机号、邮箱和身份证号，保留期 Worker 只删除可变对话内容；资金和审计事实保留。

启动 `maintenance-worker`、`review-sla-worker`、outbox、inbox 和 metrics Worker。每次发布先运行 `alembic upgrade head` 和 `alembic check`。每日运行 `scripts/backup_postgres.sh`，至少按季度在隔离库中用 `scripts/restore_postgres.sh` 演练恢复；恢复目标是 RPO 24 小时、RTO 4 小时，须记录实际结果。
