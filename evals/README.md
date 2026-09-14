# 评测集约定

每条任务都应有可验证的预期业务状态。后续 Agent 评测器将执行任务后查询业务数据库，而不是仅用 LLM 判断回答是否自然。

M1 已将案例扩展到 `m1_cases.jsonl` 的 30 条，覆盖：正常路径、规则边界、越权、重复提交、并发、非法状态迁移、内部工具失败和提示注入。`mvp_cases.jsonl` 保留为最小样例。

评测分两层进行：

1. **业务层回归测试**：不经过 LLM，验证规则、权限、确认与幂等约束不会回退；
2. **Agent 任务评测**：Agent 从自然语言完成任务后，检查售后单、状态、金额和审计日志的最终值。

只有第二层才衡量 Agent 能力；第一层保证模型无法绕过业务约束。

M1 只执行第一层：案例的 `expected` 字段描述应由服务层或 PostgreSQL 最终状态验证的结果。M2 为每条自然语言任务增加 Agent 轨迹与工具调用断言。

M2 的自然语言契约在 `m2_agent_cases.jsonl`，共 30 条。每条规定 actor、消息序列、可选的结构化确认事件、预期意图/工具/业务状态以及负向安全断言。真实 LLM 评测在配置 `LLM_*` 后执行；状态机单元测试始终使用 Fake LLM，保证没有 API key 时仍可验证确认、权限和幂等边界。

M4 的 `m4_knowledge_agent_cases.jsonl` 和 `run_m4_evals.py` 检查真实 Agent 的检索日志、引用是否属于服务端候选集、客户受众范围和零事务写入；它不以回答措辞作为通过条件。运行前应完成 M4 演示知识的索引和发布。

M5 的 `m5_fulfillment_agent_cases.jsonl` 和 `run_m5_evals.py` 先经签名 Webhook 写入已验证的取件事实，再要求真实 Agent 只调用 `get_fulfillment_status`。评测确认 Agent 不改变 `fulfillment_events`，不以自然语言措辞判定结果。运行时需要 `FULFILLMENT_WEBHOOK_SECRET`，报告仍应输出至被忽略的 `evals/reports/`。
