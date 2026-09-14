# M6 验收：质量治理与运营可观测性

验收时间：2026-09-14。M6 只消费 M1–M5 事实；以下已通过项以 PostgreSQL 行、Worker lease、告警事件和真实模型结果为准。

| 项目 | 结果 | 证据 |
|---|---|---|
| 客户反馈边界 | 通过 | `(run_id, actor_id)` 唯一；客户只能反馈自己的 Run，反馈不改变 `agent_runs`。 |
| Trace 下钻 | 通过 | 从 `AgentToolCall.result_json.id` 和 `m1_request_id` 关联至售后单和 M1 审计；API 只开放给运营角色。 |
| 指标窗口与 Worker | 通过 | 七类聚合指标写入唯一窗口快照；PostgreSQL 双 Worker 对同一窗口只有一个领取成功。 |
| 告警闭环 | 通过 | 规则版本、fingerprint 去重、领取/静默/解决乐观锁，以及 PostgreSQL append-only `ops_alert_events` 均被验证。 |
| 评测任务治理 | 通过 | Worker 在执行前校验 M2–M5 case checksum，绑定登记模型和提示词版本；逐 case 结果写入 append-only `evaluation_results`。 |
| Prometheus | 通过 | `/metrics` 导出由 PostgreSQL snapshot 刷新的聚合 gauge 和开放告警数，不含客户原文。 |
| 真实模型发布门禁 | 通过 | 经 M6 API 入队并由独立 Worker 执行 DeepSeek：M2 30/30、M3 5/5、M4 4/4、M5 1/1。 |
| 产品化闭环 | 通过 | 运营台展示窗口指标、Trace 下钻、评测明细、告警处置和规则历史；主管经幂等 API 发布规则新版本。真实 API 冒烟验证了健康检查、规则读取/发布和评测运行读取。 |
| 本地部署与采集（可选） | Compose 配置通过 | Compose 为一键演示包装，不是 M6 功能验收前置条件；本机 `docker compose config --quiet` 已通过。当前 daemon 因系统缺少 `uidmap` 无法启动，真实容器运行可在具备 Docker 的环境复验。 |

## 产品化实现边界

- 发布新规则会原子退役相同 `rule_key` 的旧 published 版本；PostgreSQL trigger 只允许已发布规则转为 retired，并拒绝修改其阈值、比较符、等级和其他历史字段，也拒绝删除 published/retired 版本。
- Trace 可按 `run_id` 或 `case_id` 聚合 Agent 工具、M1 审计、M3 审核、M4 检索、M5 履约/Outbox/事故。评测页面只展示 M6 已持久化的 `evaluation_results`，不会重新计算或改写结果。
- Compose 中的 Prometheus 只 scrape API 的聚合 `/metrics`；告警路由和长期远端存储属于生产基础设施部署配置，不在这个本地演示 Compose 内。

## 本轮验证记录

- `pytest -q`：50 passed, 11 skipped。
- `RUN_POSTGRES_INTEGRATION=1 pytest -q`：61 passed；覆盖已发布规则的篡改/删除拒绝，以及并发发布后恰有一个 active 版本。
- PostgreSQL 主库已升级至 `20260914_0017`；`alembic check` 输出 `No new upgrade operations detected.`。
- `frontend/npm run build` 成功。初始 JS 约 1.16 MB，Vite 给出 bundle 大小提示，未影响功能验证。
- 已使用本机 Docker CLI 与 Compose 插件执行 `docker compose config --quiet`，配置解析通过。
- 尝试启动 rootless daemon 时，RootlessKit 明确报错 `newuidmap: executable file not found`；该机器的 Compose 运行验证被列为可选项，不影响 M6 验收结论。
