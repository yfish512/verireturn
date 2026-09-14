# M7 验收：作品集交付

验收环境：Miniforge `verireturn`、本地 PostgreSQL、非 Docker 启动。M7 的脚本与报告均不包含真实 API 密钥。

| 条目 | 结果 | 持久化证据 |
|---|---|---|
| 非 Docker 启动 | 通过 | `start_m7_local.sh` 启动 PostgreSQL、Alembic、API、四个 Worker 和 Vite 运营台。 |
| 场景数据幂等 | 通过 | `seed_m7_scenario.py` 对 `U7001`、`O7001`、`O7003` 仅创建缺失行。 |
| Agent 确认边界 | 通过 | M7 退款在确认前为 `pending_confirmation`；确认记录为 `approved` 后才执行。 |
| 工具受控与 Trace | 通过 | 退款 run 记录资格查询、创建售后、确认；取件 run 仅记录 `schedule_pickup`。 |
| 可靠履约 | 通过 | 四个 Inbox/Fulfillment 事件按序应用，case Outbox 为 `delivered`，最终状态为 `completed`。 |
| 人工审核 | 通过 | 质量争议工单依次记录创建、领取、批准例外事件。 |
| 可观测性 | 通过 | 指标任务生成 7 个快照；演示规则生成、领取并解决告警；Trace 关联 Agent、审计、履约和 Outbox。 |
| 真实模型演示 | 通过 | `live-v1` 批次的知识、退款和人工审核 run 均记录为 `deepseek-v4-flash` 且为 `completed`。 |
| 自动验收 | 通过 | `M7_RUN_KEY=live-v1 /data/user004/miniforge3/bin/mamba run -n verireturn python -m scripts.verify_m7_demo` 输出 `status: passed`，共校验 7 项持久化不变量。 |

本轮同时修复了 `run_worker_daemon.py` 的参数隔离问题：此前 `knowledge` Worker 会误接收 daemon 的 worker 名称参数，现已在导入具体 runner 后清空该参数，四类长期 Worker 均可由 M7 启动器运行。
