# VeriReturn Recovery 实验报告

- Planner：`bounded`
- 故障 episode：300
- 策略结果：900 条
- 故障家族：inventory_changed_during_confirmation, inventory_temporarily_unavailable, refund_outcome_unknown
- Harness 错误：0

| 策略 | 自主成功率 | 安全转人工率 | 严重错误率 | 等待二次确认率 | 平均额外工具调用 |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline | 0.00% | 0.00% | 0.00% | 0.00% | 1.00 |
| fixed_retry | 33.33% | 0.00% | 33.33% | 0.00% | 1.67 |
| recovery_agent | 66.67% | 0.00% | 0.00% | 33.33% | 2.00 |

结果来自固定种子、隔离数据库 fixture 和相同领域保护机制。`awaiting_reconfirmation` 表示 Agent 发现库存等业务事实变化后停止原命令，向用户征求新的明确确认；它不计入自主完成。
