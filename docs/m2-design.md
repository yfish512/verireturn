# M2：可恢复的单 Agent 售后执行闭环

## 目标与边界

M2 让用户以中文完成订单查询、物流查询、售后资格判断、退款/换货创建和取件预约。它不引入多 Agent、RAG、真实支付或真实物流回调。

LLM 只能输出受 Pydantic 校验的意图：`query_order`、`query_logistics`、`check_eligibility`、`create_after_sales`、`schedule_pickup`。它不能指定用户身份、金额、资格、状态或内部回调。

## 执行链路

```text
Agent API 注入 actor_id
→ LangGraph 解析意图与参数
→ HTTP 工具适配层调用 M1 API
→ M1 决定权限、规则、金额和状态
→ 创建 pending_confirmation case
→ 写 agent_confirmations
→ LangGraph interrupt + PostgreSQL checkpoint
→ 用户调用结构化确认 API
→ 固定确认节点调用 M1 confirm 或 cancel
```

所有 M1 HTTP 调用带有：

- `X-Demo-User-Id`：由 Agent API 的 server-side actor 注入；
- `X-Request-Id`：`run_id:sequence_no`，关联 M1 审计与 Agent trace；
- `Idempotency-Key`：由 `thread_id + message_id + operation` 的 SHA-256 派生，网络重试稳定不重复执行。

## 确认安全性

创建售后单本身只生成 `pending_confirmation` 业务事实。`confirm_case` 和 `cancel_case` 不暴露给 LLM 意图路由：LangGraph 的 `await_confirmation` 节点以 `interrupt()` 停止，只有确认 API 收到 `{"approved": true|false}` 后才能恢复图并执行对应命令。

确认记录有用户归属和 TTL。其他用户、已处理或过期确认不能恢复图。服务重启后，PostgreSQL `PostgresSaver` 恢复同一 `thread_id` 的 checkpoint；SQLite 单测使用 `MemorySaver`。

同一 thread 在 `pending` 确认期间再次收到消息时，运行时返回原确认编号，不创建新 run、不调用 M1，也不覆盖 checkpoint。这使客户端重试和用户重复发送消息不会生成第二张待确认售后单。

## LLM 与离线测试

配置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 后，运行时使用 OpenAI-compatible Chat Completions JSON mode。没有配置密钥时使用 `KeywordIntentExtractor`，仅用于本地演示与确定性测试。无论使用哪种解析器，业务规则只在 M1 执行。

当前本地 `.env` 已配置 DeepSeek OpenAI-compatible endpoint 与 `deepseek-v4-flash`；密钥仅保存在被 Git 忽略的 `.env`。一次真实连通性验证已将“帮我退 O1001，商品没有拆封”解析为 `create_after_sales / O1001 / refund`，并验证真实换货请求能调用 M1 资格检查和创建待确认售后单。

OpenAI-compatible 解析器对 API/JSON 解析失败最多重试一次；若模型将含有明确订单业务动作的输入标为 `unknown`，才使用同一输入上的受限关键词解析器补全意图。该 fallback 不生成用户身份、退款金额、资格或状态，也不能绕过 M1。

模型提示词将发票、补发、地址修改、支付和直接完成售后明确限制为 `unknown`，即使用户给出订单号也不会将其误路由为订单查询。

同一组未支持能力还在模型调用前通过确定性 allowlist 拦截；这是工具选择边界的一部分，不依赖模型是否服从提示。

## 追踪与评测

每次运行写入 `agent_runs`；每次工具调用写入 `agent_tool_calls`，包括图节点、参数、M1 request ID、耗时、状态和业务错误码。`GET /agent/runs/{run_id}/tool-calls` 只允许运行所属用户读取。

M2 自动化测试覆盖：

1. 创建后必须进入 `awaiting_confirmation`，确认前零次调用 M1 confirm；
2. 结构化批准后才调用 confirm，拒绝时调用 cancel；
3. 同一 thread 恢复后可以预约取件；
4. 其他用户不能处理确认；
5. Agent HTTP API 不接受用户自行伪造 actor；
6. M1 全量回归、PostgreSQL 并发和审计不可变测试不回退。

自然语言评测集位于 `evals/cases/m2_agent_cases.jsonl`，有 30 条可验证案例，覆盖正常路径、追问、越权、重复消息、确认过期、提示注入、工具故障和 trace 字段。
