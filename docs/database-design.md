# 数据库设计与实现记录

本文是 VeriReturn 的数据库设计基线。任何表、状态、约束或事务边界发生变化，都必须同步更新本文和对应的 Alembic migration。

## 1. 设计目标

数据库保存可审计的业务事实，Agent 不拥有直接写库的权限。它只能调用经过参数校验、权限校验、业务规则和事务控制的工具服务。

数据库要保证：

- 订单只能由所属用户查询或操作；
- 高风险售后操作必须先创建、再确认；
- 网络重试不能生成重复售后单；
- 所有状态变化都保留审计记录；
- 评测器能够依据最终业务状态判定任务是否成功。

## 2. 当前实体

| 表 | 作用 | 核心约束 |
|---|---|---|
| `users` | 用户主体 | `id` 主键 |
| `orders` | 订单事实与售后资格输入 | `user_id` 外键；金额使用 `NUMERIC(10,2)` |
| `logistics` | 订单物流状态 | `order_id` 是主键且引用订单 |
| `after_sales_cases` | 退款、换货售后事务和状态机事实 | 用户、订单均为外键；状态与请求类型受 `CHECK` 约束 |
| `audit_logs` | 关键业务状态的只追加审计事件 | 关联售后单，并记录 actor、request ID、业务说明和时间；PostgreSQL trigger 拒绝 `UPDATE` 与 `DELETE` |
| `idempotency_records` | 写命令的重试结果 | `(actor_id, operation, idempotency_key)` 唯一，保存请求指纹和售后单 ID |
| `agent_runs` | 一次 Agent 输入的运行结果 | 关联 thread、用户、模型、输入、最终回答和失败码 |
| `agent_tool_calls` | Agent 对 M1 的工具调用轨迹 | `(run_id, sequence_no)` 唯一；保存节点、参数摘要、结果、request ID、耗时和错误码 |
| `agent_confirmations` | 等待用户确认的服务端事实 | 关联 thread、run、用户和售后单；有 pending/approved/rejected/expired 状态与过期时间 |
| `knowledge_documents` / `knowledge_document_versions` | M4 知识逻辑身份与不可变版本 | 文档有客户/运营受众；同一文档仅一版 published；发布正文只能退役不能修改 |
| `knowledge_chunks` | 可检索的知识派生数据 | `(version, ordinal)` 唯一；保存 jieba 分词文本、chunk hash、模型版本与 `vector(512)` |
| `knowledge_ingestion_jobs` | 可恢复的索引任务 | 每版本一任务；`FOR UPDATE SKIP LOCKED` 与租约支持多 worker 和崩溃恢复 |
| `knowledge_retrieval_logs` / `knowledge_feedback` | RAG 证据与质量反馈 | 保存查询摘要、服务端可见范围、候选与最终引用；反馈仅限原检索 actor |

## 3. 售后状态机

```text
pending_confirmation
  └─ 用户确认 → awaiting_pickup
      └─ 预约取件 → pickup_scheduled
          └─ 后续物流签收/退款处理 → completed

pending_confirmation
  └─ 用户取消 / 过期 → cancelled

任意状态
  └─ 不可恢复的业务或工具故障 → manual_review
```

M1 已实现全部状态。`completed` 仅接受模拟物流系统的内部回调；`manual_review` 仅接受模拟运营系统的内部调用。

## 4. 事务边界

### 创建售后单

1. 校验订单存在且属于当前用户；
2. 通过确定性规则引擎计算资格和金额；
3. 以规范化请求体计算 SHA-256 指纹，查询 `idempotency_records`；已经存在且指纹一致时返回原售后单；
4. 写入 `after_sales_cases`、`CASE_CREATED` 审计事件和幂等记录；
5. 在同一个数据库事务内提交。

唯一索引是并发场景的最后防线：两个相同请求同时通过第 3 步时，只有一个能成功写入，另一个捕获唯一约束冲突并读取既有售后单。若相同键对应不同指纹，服务返回 `IDEMPOTENCY_KEY_CONFLICT`，不会覆盖原请求。

### 确认与取件预约

确认操作只允许 `pending_confirmation → awaiting_pickup`。预约只允许 `awaiting_pickup → pickup_scheduled`。取消、完成和转人工遵守状态机。每一次变化都与对应审计事件、幂等记录一起提交，保证不会出现“状态已变但无审计记录”的半完成状态。

### 种子数据的外键顺序

种子数据在同一事务中按 `users → orders → logistics` 顺序写入，每一层写入后显式 `flush`，再创建下一层记录。PostgreSQL 会在插入时立即校验外键；不能依赖 SQLite 对批量插入顺序的宽松行为。这条经验也适用于后续工具服务：父记录需要在创建子记录前对当前事务可见。

## 5. 数据类型和索引原则

- 金额：使用 PostgreSQL `NUMERIC(10,2)` 与 Python `Decimal`，绝不使用浮点数计算金额；
- 时间：PostgreSQL 使用 `TIMESTAMP WITH TIME ZONE`，应用以 `datetime.now(timezone.utc)` 写入，并在每个 PostgreSQL 连接上设置 `timezone=UTC`，使 API 稳定输出带 `+00:00` 的 ISO-8601 时间。`20260913_0002` 将初始 schema 的无时区字段按既有 UTC 含义转换，避免把服务器本地时区误当成业务时间；
- 主键：MVP 保留可读订单号，售后单使用自增整数；后续若需要跨服务生成可切换为 UUID；
- 外键：售后单必须关联用户和订单，物流必须关联订单；
- 索引：`orders.user_id`、`after_sales_cases.user_id`、`after_sales_cases.order_id` 和 `(user_id, status, created_at)`；
- 幂等：`idempotency_records` 对 `(actor_id, operation, idempotency_key)` 建唯一约束，并将请求指纹与资源 ID 一同保存。不同操作可有相同 key；同一操作同一 actor 的 key 不能重用到不同请求。

## 6. SQLite 与 PostgreSQL 的分工

- SQLite：单元测试和本地快速试验。每个测试都使用独立临时数据库；
- PostgreSQL：开发集成测试、Agent 评测和最终演示。它支持更完整的事务、并发与索引行为；
- 不允许根据 SQLite 的宽松行为判断生产正确性；涉及并发幂等、事务隔离和索引的用例必须在 PostgreSQL 中补测。

## 7. 迁移策略

已经接入 Alembic。初始 schema revision 为 `20260913_0001`，`20260913_0002` 统一业务时间为带时区的 UTC 时间，`20260913_0003` 增加 M1 状态机约束、审计 actor 与通用幂等记录，`20260913_0004` 在 PostgreSQL 数据库层将审计日志设为只追加，`20260914_0005` 增加 M2 的 Agent run、工具 trace 与确认记录。PostgreSQL 启动后的首个固定步骤是：

```sh
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  /data/user004/miniforge3/bin/mamba run -n verireturn alembic upgrade head
```

之后所有 schema 变动都通过 Alembic migration 完成：

```text
修改 SQLAlchemy model
→ 生成并人工审阅 migration
→ 在空 PostgreSQL 与已有开发数据库上执行
→ 运行业务集成测试和评测冒烟
→ 更新本文档
```

不会使用 `Base.metadata.create_all()` 修改已有 PostgreSQL schema；该方法只保留给 SQLite 测试与首次本地原型。

LangGraph `PostgresSaver` 的 `checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 和 `checkpoint_migrations` 由该库的 `setup()` 生命周期管理，不由 Alembic 迁移。Alembic 的 `include_object` 显式排除这些外部表，避免把运行时 checkpoint 基础设施误报为 schema drift。

M4 的 `20260914_0011` 启用每数据库一次的 `vector` extension，并建立版本化知识表、全文 GIN 表达式索引、HNSW 向量索引和已发布版本的不可变 trigger。表达式/HNSW 索引由该 migration 手工维护；Alembic 的 drift 检查将它们排除，以免 SQLite 兼容 metadata 错误提议删除生产索引。`20260914_0012` 将检索日志的服务端受众范围从 16 扩展到 64 字符，以完整保存 `customer|operator|shared` 这类审计事实。

## 8. 本地依赖与运行边界

数据库相关组件全部由 Miniforge 环境 `verireturn` 管理：

| 组件 | 用途 | 选择原因 |
|---|---|---|
| PostgreSQL 18 | 集成测试、演示和并发语义基线 | 外键、事务、唯一约束和索引行为与目标部署一致 |
| SQLAlchemy 2 | 模型映射、会话和事务边界 | 业务服务不拼接 SQL，便于测试和替换数据库 |
| psycopg 3 | Python 到 PostgreSQL 的数据库驱动 | SQLAlchemy 的 `postgresql+psycopg` 连接方言 |
| Alembic | schema 版本控制 | 让表结构改变可审阅、可升级、可回退 |
| pgvector | M4 向量列和 HNSW 相似检索 | 仍由 PostgreSQL 承载业务事实与检索派生数据，无独立向量数据库 |
| FastEmbed + bge-small-zh-v1.5 | 固定的本地中文 embedding | 不新增 API 密钥；模型与缓存路径显式配置并可预热 |

本地 server 的数据目录为 `.local/postgres`，端口为 `54329`，两者均不进入 Git。启动脚本只运行当前用户的进程，无需 Docker 或系统级 PostgreSQL 安装。连接地址由 `DATABASE_URL` 显式传入进程；测试用 SQLite 与演示用 PostgreSQL 的职责不混用。
