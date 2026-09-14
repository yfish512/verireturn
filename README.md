# VeriReturn

VeriReturn 是一个面向中文电商售后的可验证事务型 Agent 项目。第一阶段先实现可测试的模拟业务后端：订单、物流、退款/换货资格、售后单、确认执行与取件预约。Agent 只能通过受约束的工具操作业务数据。

## M1：可验证售后工具后端

- 查询订单和物流
- 根据确定性业务规则判断退款或换货资格
- 创建待确认的售后单；确认、取消、预约、完成均受状态机约束
- 所有写接口使用幂等键，PostgreSQL 并发重试只产生一次业务结果
- 审计事件记录操作者、请求 ID 和状态变化
- 为后续 Agent 评测准备可校验的业务最终状态

## Python 与数据库环境

Python 依赖和 PostgreSQL server 都通过 Miniforge 的 `verireturn` 环境管理，不需要系统级安装数据库。首次或依赖更新后执行：

以下命令假设 `mamba` 已在 `PATH` 中；若未初始化 shell，可显式设置 `MAMBA_BIN=/path/to/miniforge/bin/mamba`。

```sh
mamba env update -f environment.yml
```

启动环境内的本地 PostgreSQL：

```sh
mamba run -n verireturn sh scripts/local_postgres.sh start
```

数据库服务第一次启动后，执行首个 schema migration：

```sh
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  mamba run -n verireturn alembic upgrade head
```

`.env.example` 是部署环境变量的样例；当前开发命令直接显式传入 `DATABASE_URL`，避免把连接配置隐式写入代码。

完整设计见 [数据库实现记录](docs/database-design.md)。
M1 的逐项验收和演示见 [M1 验收说明](docs/m1-acceptance.md)。

## M2：单 Agent 售后执行闭环

M2 用 LangGraph 将中文自然语言请求转成受约束的 M1 工具调用。模型只解析意图和缺失信息；退款资格、金额、权限、状态机和数据库写入仍由 M1 决定。创建售后单后，图会中断并持久化，只有确认 API 的结构化 `approved` 事件才能触发确认或取消。

完整架构、状态图和验收见 [M2 实现记录](docs/m2-design.md)。

## M3：售后运营闭环（设计）

M3 规划增加人工审核队列、策略版本化、审核审计、运营指标和工作台，并保持 M1 的状态机和 M2 的客户确认边界。完整设计及向量数据库决策见 [M3 设计](docs/m3-design.md)。

M3 已实现并通过验收；运行、验收证据和运营工作台说明见 [M3 验收说明](docs/m3-acceptance.md)。

## M4：证据驱动知识 Agent

M4 用 PostgreSQL `pgvector`、中文全文检索和本地 `BAAI/bge-small-zh-v1.5` embedding 支持带引用的售后政策问答。知识库包含文档版本、发布/退役、可靠索引任务、客户/运营受众隔离、检索日志与反馈；RAG 只能解释已发布规则，不能决定订单资格、金额、权限、状态或审核结果。

首次使用 M4 前，准备固定的本地模型文件：

```sh
mamba run -n verireturn sh scripts/bootstrap_embedding_model.sh
```

然后可装载原创演示语料、索引并发布：

```sh
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  mamba run -n verireturn python -m scripts.seed_m4_knowledge
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  mamba run -n verireturn python -m scripts.run_knowledge_worker
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  mamba run -n verireturn python -m scripts.publish_m4_demo_knowledge
```

完整边界、表结构和评测见 [M4 设计](docs/m4-design.md) 与 [M4 验收](docs/m4-acceptance.md)。

## 运行 API

```sh
mamba env create -f environment.yml
mamba run -n verireturn sh scripts/local_postgres.sh start
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  mamba run -n verireturn uvicorn backend.app.main:app --reload
```

服务启动后访问 `http://127.0.0.1:8000/docs`。

Agent 请求示例：

```sh
curl -X POST http://127.0.0.1:8000/agent/threads/demo-thread-1/messages \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-User-Id: U001' \
  --data '{"message":"帮我退 O1001，商品没拆封","message_id":"demo-message-001"}'
```

响应为 `awaiting_confirmation` 时，使用返回的 `confirmation_id` 调用 `POST /agent/confirmations/{confirmation_id}`，body 为 `{"approved": true}` 或 `{"approved": false}`。

默认示例配置使用 DeepSeek 的 OpenAI-compatible 接口和 `deepseek-v4-flash`。将实际 `LLM_API_KEY` 写入本地 `.env`；该文件已被 Git 忽略，不能将密钥写入代码、文档或提交记录。

开发环境使用 `X-Demo-User-Id` 演示身份注入。生产部署设定 `APP_ENV=production` 和 `AUTH_MODE=jwt`，再配置至少 32 字符的 `AUTH_JWT_SECRET`；服务将只接受 HS256 Bearer JWT 的 `sub`，并仍从数据库读取角色，绝不信任 token 或请求中的 role 字段。

## 初始示例

`U001` 的订单：

- `O1001`：3 天前签收、未拆封，可退款。
- `O1002`：10 天前签收、已拆封、无质量问题，不可无理由退款。
- `O1003`：15 天前签收、有质量问题，可换货。

创建售后单时，用户身份和幂等键必须由服务端上下文/HTTP header 提供：

```json
{
  "order_id": "O1001",
  "request_type": "refund",
  "reason": "商品未拆封，想退款"
}
```

示例 header：`X-Demo-User-Id: U001`、`Idempotency-Key: demo-refund-o1001-v1`。创建后，必须调用确认接口，售后单才会进入执行状态。

## M5：事件驱动履约与主动服务

M5 将“预约取件”后的异步流程实现为 PostgreSQL Transactional Outbox、Provider Inbox 和不可变履约事实。取件预约与 `pickup.requested` Outbox 在一个事务中提交；Worker 使用 `FOR UPDATE SKIP LOCKED`、租约、投递流水和退避重试。演示 Provider 只确认请求，不会伪造物流回调。

Provider 回调只接受 `POST /internal/fulfillment/webhooks/demo_fulfillment`，需要原始 body 的 `X-Provider-Signature`（HMAC-SHA256）、五分钟内的 `X-Provider-Timestamp`、以及与 body 一致的 `X-Provider-Event-Id`。重复事件不重复推进状态；乱序事件停留在 Inbox 等待运营重放，并出现在运营台的履约事故队列。

退款状态为 `pickup_scheduled → picked_up → return_received → refund_processing → completed`；换货状态为 `pickup_scheduled → picked_up → return_received → replacement_shipped → completed`。Agent 可以用“售后单 #12 进度到哪了”读取已持久化的履约事实，不能写入履约状态或触发回调。

运行独立 Worker：

```sh
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  mamba run -n verireturn python -m scripts.run_fulfillment_worker
```

完整事件契约、失败语义与验收矩阵见 [M5 设计](docs/m5-design.md)。

## M6：质量治理与运营可观测性

M6 将 Agent、审核、知识与履约事实关联为可下钻 trace，并用 PostgreSQL Worker 计算指标、生成去重告警和持久化评测结果。运营主管可创建窗口指标任务和 M2–M5 评测任务；Worker 不会修改 M1–M5 业务事实。

```sh
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  mamba run -n verireturn python -m scripts.run_metrics_worker
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  EVAL_BASE_URL=http://127.0.0.1:8000 \
  mamba run -n verireturn python -m scripts.run_evaluation_worker
```

设计和验收见 [M6 设计](docs/m6-design.md)。

## M7：可演示、可复核、可投递

M7 将 M1–M6 固定为非 Docker 的本地作品集演示：脚本幂等写入独立 fixture，经 Agent、审核、验签履约、指标、告警和 Trace 跑完整业务闭环；验收器只检查 PostgreSQL 中持久化的业务事实。

```sh
M7_AGENT_MODE=live sh scripts/start_m7_local.sh
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  mamba run -n verireturn python -m scripts.demo_m7
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  mamba run -n verireturn python -m scripts.verify_m7_demo
```

运行方式、讲解顺序见 [M7 演示手册](docs/m7-demo-script.md)，架构和复现边界见 [M7 设计](docs/m7-design.md) 与 [M7 架构](docs/m7-architecture.md)，验收证据见 [M7 验收](docs/m7-acceptance.md)，简历与面试材料见 [M7 求职材料](docs/m7-resume.md)。

### 客户 Agent 展示页

启动本地运行时后访问 `http://127.0.0.1:5173/agent`。该页面通过真实 Agent API 展示对话、发布知识引用、受控工具调用、待确认命令和履约事件；运营治理界面仍位于根路径 `http://127.0.0.1:5173`。前端只渲染服务器返回的事实，不参与资格、金额、权限或状态决策。

## 开源与贡献

项目采用 [MIT License](LICENSE)。提交代码前请阅读 [贡献指南](CONTRIBUTING.md) 和 [安全政策](SECURITY.md)。GitHub Actions 会执行 Python 单元/API 测试、PostgreSQL 集成测试和运营台生产构建；CI 不使用真实模型密钥、真实订单或本地运行产物。

### 可选：M6 本地产品化演示（Docker Compose）

Docker Compose 只用于一键启动演示环境，不是开发、验收或运行 Agent 核心链路的前置条件。具备 Docker 环境时，准备本地 `.env`（以 `.env.example` 为模板，真实 DeepSeek 密钥只保存在本机）后，可启动包含 PostgreSQL/pgvector、API、履约与指标 Worker、评测 Worker、运营台和 Prometheus 的环境：

```sh
docker compose up --build -d
sh scripts/demo_m6.sh
```

运营台位于 `http://127.0.0.1:5173`，Prometheus 位于 `http://127.0.0.1:9090`，API 文档位于 `http://127.0.0.1:8000/docs`。演示脚本会创建一个十分钟指标窗口；指标 Worker 会领取任务，随后打印运营概览。`metrics-worker`、`fulfillment-worker`、`knowledge-worker` 与 `evaluation-worker` 都是独立的持续进程，业务任务仍通过数据库租约和幂等约束协调。

首次启动时，`embedding-bootstrap` 会把固定的 FastEmbed 中文模型下载到命名卷，API 与 `knowledge-worker` 共享该卷；离线环境应先预热镜像或该模型卷。Prometheus 的 `/metrics` 仅用于受信任的本地或内网采集环境。

#### Rootless Docker 前置条件（可选）

Rootless Docker 需要主机管理员安装 `uidmap`，它提供 `newuidmap` 和 `newgidmap`，用于建立容器的用户 ID 映射。Ubuntu/Debian 管理员可执行：

```sh
apt-get update
apt-get install -y uidmap
```

随后为运行 Docker 的用户配置 `/etc/subuid`、`/etc/subgid`，按 Docker 官方 rootless 安装流程启动 daemon，并设置该 daemon 对应的 `DOCKER_HOST`。先执行 `docker compose config --quiet`，再执行 `docker compose up --build -d`。
