# M4 验收：证据驱动知识 Agent

验收时间：2026-09-14。

| 项目 | 结果 | 证据 |
|---|---|---|
| pgvector 与本地模型可用 | 通过 | Miniforge PostgreSQL 18 已启用 `vector 0.8.5`；实际 `BAAI/bge-small-zh-v1.5` 推理输出 512 维向量。 |
| 文档版本和发布不变量 | 通过 | 单元/API 测试覆盖草稿、入队、索引、发布、旧版本退役、幂等；PostgreSQL 测试拒绝篡改已发布正文。 |
| 中文混合检索 | 通过 | PostgreSQL migration 创建 `jieba + tsvector` GIN 与 pgvector HNSW 索引；集成测试实际执行两条 SQL 检索腿并回传 RRF 结果。 |
| 权限与引用边界 | 通过 | 客户只获得 `customer/shared` 证据；运营 SOP 不会进入客户候选集；服务端拒绝不在候选集中的引用。 |
| Agent 写入隔离 | 通过 | 知识问答只产生 `retrieve_knowledge` trace；测试断言零 M1 工具调用、零售后单写入。 |
| 真实模型评测 | 通过 | `evals/run_m4_evals.py` 使用本地 DeepSeek 配置执行 4/4：退款政策、取件流程、提示注入、运营 SOP 受众隔离。报告位于被忽略的 `evals/reports/m4-deepseek-accepted.json`。 |

M4 语料已在本地 PostgreSQL 演示库完成 4 份原创文档的“创建 → 本地 embedding → 索引 → 发布”闭环。评测检查数据库事实、工具 trace、候选集合和引用范围，不以自然语言表述作为通过依据。

M4 改动后重新执行既有真实模型回归：M2 为 30/30（66.591 秒），M3 为 5/5（7.015 秒）；报告分别为被忽略的 `m2-deepseek-post-m4.json` 与 `m3-deepseek-post-m4.json`。这确认知识路由没有改变既有的售后确认和人工审核路径。

最终回归命令与结果：

```text
RUN_POSTGRES_INTEGRATION=1 pytest -q
42 passed, 1 warning

alembic check
No new upgrade operations detected.

cd frontend && npm run build
TypeScript + Vite production build succeeded.
```

前端构建仍提示 Ant Design 导致初始 JavaScript 约 1.15 MB；这是不阻塞功能和验收的性能后续项。
