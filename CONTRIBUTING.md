# 贡献指南

欢迎提交 issue 和 pull request。提交前请先阅读各阶段设计文档，并遵守以下边界：

- 不要提交 `.env`、API 密钥、真实订单/客户数据、本地 PostgreSQL 数据、模型缓存或评测运行产物。
- Agent 只能做意图解析、知识解释和受控工具编排；资格、金额、权限和状态迁移必须保留在领域服务中。
- 写路径必须具备幂等键、可审计事件和明确的权限校验。新增异步外部动作应遵循 M5 Outbox/Inbox 模式。
- 新增 PostgreSQL 特性时，请同时覆盖并发、回滚或不可变性行为；SQLite 单测不能替代这些验证。

本地检查：

```sh
/data/user004/miniforge3/bin/mamba run -n verireturn pytest -q
RUN_POSTGRES_INTEGRATION=1 \
  /data/user004/miniforge3/bin/mamba run -n verireturn pytest -q
(cd frontend && npm ci && npm run build)
```

提交信息使用动词开头的简短描述。PR 应说明业务触发条件、状态变化、测试结果，以及是否改变了 Agent 或权限边界。
