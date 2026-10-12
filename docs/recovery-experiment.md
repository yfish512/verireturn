# VeriReturn Recovery：设计、运行与恢复边界

Recovery 回答的是“已授权的工具操作出问题后，Agent 能否根据反馈调整下一步并完成售后”，覆盖读取失败、写操作结果未知和确认期间库存变化。主售后 Agent 同时提供完整对话入口；实验单独隔离恢复决策。

## 实现与分工

技术栈沿用 Miniforge Python、FastAPI、LangGraph、SQLAlchemy/Alembic、PostgreSQL、DeepSeek API 和 React。恢复步骤存入 PostgreSQL `RecoveryExecution/RecoveryStep`；主对话复用 PostgreSQL checkpoint。实验使用隔离的文件 SQLite 领域库，退款模拟渠道另存 `channel.db`，从而复现渠道已提交、本地未回写的崩溃窗口。PostgreSQL 锁、租约与预算另有并发测试。

```mermaid
flowchart LR
  A[原工具反馈] --> B[持久化观察与未决操作]
  B --> C[模型选择允许动作]
  C --> D[权限与业务事实校验]
  D --> E[受控工具执行]
  E --> F[核验数据库与渠道事实]
  F --> B
  F --> G[完成 / 等待客户 / 等待补货 / 人工]
```

模型负责理解公开反馈、选择动作并解释原因。领域服务负责金额、数量、权限、资格、预占及状态变化；模型不能直接写库。退避、幂等和预算升级属于系统机制，单独记录，不能算成模型贡献。

每轮最多 8 次工具、4 次规划、120 秒；读操作重试最多两次。HTTP 客户端取消统一自动重试，模型 SDK `max_retries=0`，25 秒请求超时；DeepSeek 使用 JSON 输出、temperature=0、max_tokens=1024、关闭 thinking。记录配置模型和服务返回的实际模型名称。

写操作超时先核验原幂等命令及渠道。只有渠道返回可信未执行证据才重试；没有本地记录不等于未执行。主链路遇到无法证明结果时保留未决，停止或创建实际工单。状态变化先刷新方案，再取得客户新授权。

预算耗尽保留 `budget_exhausted`，并执行单独记录的系统人工升级命令（`system_actions/system_escalation`），不重置模型预算、不静默切成规则 Agent、不计自主完成。没有关联售后单的工具故障只能安全停止，不能假称已创建工单。

## 客户方案与业务闭环

- 原 SKU 或允许替换 SKU：服务端允许列表、库存行锁、预占与履约事实；签收才算完成。
- 换货转退款：一次事务取消原单、释放预占与原处理占用，建立关联退款单并复制订单行实付金额。取件与退货事实触发退款 Outbox；已验签渠道成功事件驱动结算与数量同步。
- 等待补货：保持开放任务，补货后发布新方案并通知客户，不能沿用旧确认。
- 人工、拒绝、无回复、撤回：分别持久化；拒绝或未回复不能执行新方案。

确认绑定方案版本、15 分钟有效期、业务快照与库存版本。重复确认返回原结果，冲突确认拒绝；已出库换货异常只允许人工，避免同时寄出替换商品和退款。恢复记忆按领域事实纠正，转换后使用新退款单及正确售后类型。

## 评测集与对照

| 家族 | 每个变体 25 条 |
| --- | --- |
| 读取异常 | 一次失败、两次失败、持续不可用、核验阶段再次失败 |
| 写结果未知 | 提交前失败、渠道成功本地未回写、本地成功响应丢失、查询暂不可用 |
| 确认期间变化 | 原 SKU 补货、允许替换 SKU、改为退款、等待/人工/拒绝/无回复/撤回 |

共 300 个参数化任务：商品、数量、金额、库存、故障步骤和客户表达实际变化；固定种子生成，每变体固定划分 20 个 development / 5 个 holdout。用户脚本只按公开选项回应。故障脚本按操作发生次数或确认事件触发，两种策略面对相同外部条件。模型上下文不含变体标签、注入规则、预期结果和标准答案，工具文本不能覆盖系统权限。

离线运行 `baseline`、`fixed_retry`、规则 `recovery_agent`；真实模型只比较 `react_baseline` 与模型 `recovery_agent`。普通模型同样能够看反馈并决定下一步；恢复模型额外获得未决执行证据、重试记录及恢复指导。两者共用全部业务防护，模型错误不得降级为规则成功。

正式实验 v2 在调优后冻结，600 条结果原样保存。最终代码使用 v3 恢复提示与进一步崩溃加固；专项结果单独报告，不替换 v2 正式估计。旧三个固定场景的 300 episode 规则结果保留在 [历史报告](recovery-legacy-report.md)，不作为模型证据。详细结果见 [真实实验报告](recovery-latest-report.md)。

## 判定与成本

独立判定器读取数据库、模拟渠道账本、授权方案和履约记录。分别记录实际业务违规、错误动作、拦截动作和错误完成判断。退款必须满足渠道成功、金额匹配和订单行已退款数量；换货必须收到签收事实。完成分自主与客户协助，人工、等待补货和等待回复独立统计。

成本同时报告总工具、模型请求、Token、耗时、重试、核验及系统升级次数。额外工具为相对配对无故障**规则路径**的增量；另外实际运行分层 30 条无故障模型对照。模型请求失败也消耗共享预算，usage 不可得时记录未知成本而不假称免费。

## 持久化与并发

`RecoveryExecution` 保存版本、未决命令、业务引用、预算和租约，`RecoveryStep` 保存有序 dispatch、观察、决策、请求 usage 和停止原因。副作用前先存 dispatch，重启先核验，不盲目重复写入。恢复所有权按客户及会话校验，版本在执行锁内再次校验。

`EvaluationSuite` 保存完整 JSONL 内容快照和 SHA256，改变数据生成新版本。评测逐条事务提交，续跑跳过已提交结果并复用隔离 fixture。Worker 心跳续租，结果及完成提交均校验 lease token；过期旧 Worker 不能写结果。运行列表返回 v2 摘要，明细从结果接口分页读取，避免重复传输整份轨迹。大结果查询在 SQL 中过滤、分页，Worker 自身也分页读取续跑结果。

所有真实恢复规划请求共享 `recovery-v2-implementation` 预算，PostgreSQL 条件 UPDATE 原子预留，累计上限 3000，重启或并发不能重置。SDK 失败同样计数；主 Agent 的意图抽取调用不属于这项恢复规划预算。预算快照保存于验收证据。

请求上下文投影脱敏；隐私删除清理恢复目标、原因、观察、决策与步骤文本，保留业务引用、计数和有序占位，确保继续核验时不重复步骤编号。保留期 Worker 同样清理到期恢复上下文，财务与审计事实按原项目规则保留。

## API

| 接口 | 用途 |
| --- | --- |
| `GET /ops/recovery/scenarios` | 家族及变体目录 |
| `POST /ops/recovery/compare` | 每家族抽一个样本，同步小规模比较；seed 控制选样 |
| `POST /ops/evaluation-runs` | 全量异步评测，策略/规划模式/预算/重复次数 |
| `GET /ops/evaluation-runs/{id}/results` | `limit/offset/strategy/scenario_id` 过滤分页 |
| `GET /agent/recoveries/{id}` | 归属校验、步骤分页、可用方案 |
| `POST /agent/recoveries/{id}/resume` | expected_version，沿原预算续跑 |
| `POST /agent/recoveries/{id}/cancel` | 停止自动恢复，不等于取消已提交退款 |
| `GET /tools/after-sales/cases/{id}/resolution-options` | 领域计算可用方案 |
| `POST /tools/after-sales/cases/{id}/resolutions` | 幂等提案，option 与 replacement_sku |
| `POST /tools/after-sales/resolutions/{id}/confirm` | expected_version + approved，再次确认 |

全量入队示例：

```json
{"suite_key":"recovery","prompt_version":"recovery-prompt-v3","planner_mode":"llm","strategies":["react_baseline","recovery_agent"],"max_model_calls":4,"max_tool_calls":8,"repetitions":1,"model_name":"deepseek-v4-flash"}
```

缺模型地址/密钥时拒绝真实实验；v3 代码不能冒充旧 v2 Prompt 重新评测。生产环境禁止故障注入及恢复实验创建，主客户恢复不使用故障脚本。

## 本地运行与复核

先按 [项目总览](project-overview.md) 启动 Miniforge 环境、PostgreSQL、API、Worker 和前端，将 `DATABASE_URL` 指向已迁移的数据库。密钥通过本地 `.env`/环境注入，绝不提交。

```sh
export DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn
mamba run -n verireturn alembic upgrade head
mamba run -n verireturn python -m evals.run_recovery_evals --planner bounded --report /tmp/offline.json
mamba run -n verireturn python -m evals.run_recovery_evals --planner llm --limit 12 --report /tmp/smoke.json
# 正式全量会使用共享剩余预算；预算不足保存结果及停止原因。
mamba run -n verireturn python -m evals.run_recovery_evals --planner llm --report /tmp/formal.json
# 只有 manifest 的样本、Prompt、策略及实现校验和完全一致才可以续跑。
mamba run -n verireturn python -m evals.run_recovery_evals --planner llm --report /tmp/formal.json --resume
mamba run -n verireturn python scripts/generate_recovery_report.py /tmp/formal.json
```

前端 `/agent` 提供客户方案、再确认、停止/继续恢复及刷新恢复；运营首页评测“查看明细”提供同一任务并排轨迹、最终业务事实及成本。

```sh
mamba run -n verireturn python -m scripts.demo_recovery_v2
mamba run -n verireturn playwright install chromium
mamba run -n verireturn python -m scripts.verify_recovery_browser
# 实际浏览器选择退款并再次确认，创建隔离验收订单。
mamba run -n verireturn python -m scripts.prepare_recovery_browser
mamba run -n verireturn python -m scripts.verify_recovery_browser --exercise-choices
```

归档的正式 CLI 结果可运行 `python -m scripts.import_recovery_report` 导入开发运营台；保留原 Prompt、套件快照和导入校验和，不重新请求模型，也不冒充新一轮实验。

浏览器可通过 `PLAYWRIGHT_CHROMIUM_PATH` 指定已有 Chromium。演示不接入真实支付账户；JWT/OIDC、支付 Provider 生产替换和真实用户分布仍需要部署验证。完整验收见 [验收报告](recovery-acceptance.md)，页面与实测图表见 [成果展示](showcase.md)。
