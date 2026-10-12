# VeriReturn Recovery：工具故障下的 Agent 恢复与评测

Recovery 模块评估的不是接口是否会自动重试，而是 Agent 收到工具反馈后能否选择安全的下一步行动。它复用真实的订单、订单行、库存预占、退款意图和支付提交领域服务；故障只注入在工具适配层。

## 对照策略

| 策略 | 行为 |
| --- | --- |
| `baseline` | 工具失败后停止，不做恢复。 |
| `fixed_retry` | 对错误统一再调一次工具，不核验写操作是否已执行。 |
| `recovery_agent` | 在允许动作集合中选择有限重试、领域状态核验、刷新业务状态、替代方案或转人工。 |

`recovery_agent` 可使用固定的 `BoundedRecoveryPlanner` 做可重复离线基线，也可以在设置 `LLM_BASE_URL`、`LLM_API_KEY` 后使用 OpenAI-compatible 模型规划。无论哪种 Planner，模型输出都会在本地检查，只能选择系统给出的允许动作。

## 场景

1. **库存查询临时不可用**：读取工具首次 503。恢复 Agent 只做一次受限重试，失败则安全转人工。
2. **退款提交结果未知**：退款提交已经持久化，但工具边界返回超时。恢复 Agent 必须查询 `RefundIntent`，发现 `submitted` 后停止重试。
3. **确认期间库存被占用**：换货确认前库存发生变化。恢复 Agent 刷新领域状态，提供退款、等待补货或人工处理，并要求新的用户确认。

每个策略和任务都使用全新的 SQLite fixture。它们共享相同的领域服务、状态机和幂等约束，避免将后端保护误当作 Agent 能力提升。

当前评测集包含 300 个隔离、固定种子的故障 episode：三个故障家族各 100 条。它用于检验同一类恢复行为在重复执行下的稳定性，不应表述为 300 个不同语义的业务模板。三个策略均运行同一组 episode，因此一次完整评测产生 900 条策略结果。最新可复现基线见[实验报告](recovery-latest-report.md)。

## 运行

```sh
mamba run -n verireturn python -m evals.run_recovery_evals \
  --cases evals/cases/recovery_agent_cases.jsonl \
  --report /tmp/recovery.json
mamba run -n verireturn python scripts/generate_recovery_report.py /tmp/recovery.json
```

如需用模型比较恢复决策：

```sh
mamba run -n verireturn python -m evals.run_recovery_evals --planner llm
```

运营 API：

```text
GET  /ops/recovery/scenarios
POST /ops/recovery/compare
```

接口只运行隔离实验 fixture，不读取或修改生产订单、库存、退款与用户记录。

## 指标定义

- **自主成功率**：在无需人工介入的情况下达到预期业务状态。
- **安全转人工率**：无法自主恢复，但没有产生错误业务副作用。
- **严重错误率**：例如结果未知时盲目重复退款提交。
- **等待二次确认率**：业务事实发生变化后等待用户重新选择；不计入自主完成。
- **平均额外工具调用数**：相对无故障路径的恢复成本。

评测报告保留每一步的故障注入、工具调用、恢复动作、最终领域状态和固定随机种子。不要将转人工或等待二次确认计入自主完成。
