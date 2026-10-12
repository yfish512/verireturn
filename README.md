# VeriReturn

面向电商售后的可验证事务型 Agent：模型理解客户目标、选择工具和恢复动作，领域服务决定金额、权限、库存与最终业务状态。

## 项目成果

- **完整售后链路**：LangGraph 对话、订单行级部分退款、库存预占与换货履约、人工审核、支付回调与对账。
- **可恢复任务记忆**：多任务切换、确认绑定版本与有效期、崩溃后按业务事实纠正记忆，支持刷新及重启续跑。
- **可靠业务执行**：PostgreSQL 事务、行锁、Outbox/Inbox、幂等、租约与审计，覆盖重复、乱序及结果未知的操作。
- **模型恢复与真实对照**：12 个变体、300 个参数化故障任务；冻结 v2 实验中，同一 DeepSeek 模型和业务保护下，完整业务完成率 **66.67% → 83.00%**，配对提升 **16.33 个百分点**（95% CI：12.33–20.67）。自主完成与客户协助完成分别统计。
- **可复核证据与展示**：独立读取业务及模拟渠道事实判定，持久化轨迹、失败案例和成本；React 展示客户恢复与同任务策略对照，原始结果可离线复核。

![真实模型对照结果](docs/images/recovery-results.png)

收益集中在读取异常；恢复策略输入 Token 增加约 21.8%。上述结果来自冻结 v2；当前 v3 仅完成 47 条修复专项，不作为新的全量效果估计。[数据、失败分析与边界](docs/recovery-latest-report.md)

![客户恢复页面：重新确认后继续任务](docs/images/customer-recovery.png)

[更多截图与图片生成方法](docs/showcase.md)

## 技术栈与验证

`Python` · `FastAPI` · `LangGraph` · `PostgreSQL` · `SQLAlchemy/Alembic` · `DeepSeek` · `pgvector` · `Redis` · `React/TypeScript`

119 项后端单元/API 测试、16 项 PostgreSQL 集成测试通过；迁移检查、前端构建、真实 API 退款/换货演示及浏览器再确认通过。项目使用模拟支付渠道及开发身份，未部署真实支付账户；两组实验均无实际业务违规，不把系统保护算成模型提升。

[项目流程与运行](docs/project-overview.md) · [Recovery 设计](docs/recovery-experiment.md) · [真实实验报告](docs/recovery-latest-report.md) · [验收证据](docs/recovery-acceptance.md)

## License

[MIT](LICENSE)
