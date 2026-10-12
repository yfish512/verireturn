# Recovery 可复核证据

归档按实验阶段区分，不重新挑选成功样本、不替换失败结果。

| 文件前缀 | 内容 |
| --- | --- |
| `formal-v2` | 300 故障任务 × 普通/恢复模型 = 600 条；正式效果来源 |
| `no-fault-v2` | 分层 30 配对无故障 × 两模型 = 60 条 |
| `repeat-v2` | 分层 30 保留任务重复 × 两模型 = 60 条 |
| `offline-fault-v2` / `offline-no-fault-v2` | 各 300 × 三规则策略 = 900 条 |
| `recovery-v2-live-smoke*` | 开发冒烟，包括截断/失败和调优记录，不并入正式统计 |
| `repair-v3-before-escalation` | 修复持续故障及提示后的 47 条专项 × 两模型；保留预算耗尽问题 |
| `repair-v3-final` | 同一 47 条、加入显式系统预算工单升级；不作为新的总体效果估计 |
| `*.cases.jsonl` | 无故障/重复/专项/冒烟实际样本，不依赖外部临时路径 |
| `protocol.json` / `protocol-source.zip` | 正式 v2 核心源码与 300 任务的冻结 SHA256 |
| `repair-protocol*` / `final-protocol*` | 两个专项阶段的核心源码；Prompt、环境和判定变动分别保留 |
| `release-source.zip` | 交付版本 backend、tests、migrations、evals、scripts、frontend 和环境依赖源码 |
| `budget.json` | PostgreSQL 共享 3000 次预算的实际最终快照 |
| `live-tool-injection.json` | 两种真实模型面对工具内容注入的补充安全验证，不并入正式效果 |
| `api-demo.json` / `business-facts.json` | 运行中 FastAPI 退款及换货完成、业务表与已验签回调事件、审计核对 |
| `*-tests.txt` / `migration-check.txt` / `frontend-build.txt` / `browser-interaction.txt` | 验收日志 |
| `SHA256.json` | 证据文件内容校验和，不包含清单自身及本说明 |

`.results.jsonl.gz` 保存每条结果（包括失败）、轨迹、使用量与最终事实；`.summary.json` 保留原报告除明细以外的字段。数字从原始行计数，不能从四舍五入后的均值倒算。正式模型配置 `deepseek-v4-flash`，服务返回 usage 中模型名为 `deepseek-flash`。

源代码冻结只保证输入和实现可以复核；远程模型即使 temperature=0 也可能波动，不能保证重新请求得到完全相同输出。原正式“持续失败”仅约束 primary 查询的环境缺口已在最终版本修复，并在报告披露。专项包含保留样本的事后分析，不再声称是未经调优的独立保留测试。

离线复核（不消耗模型预算）：

```sh
mamba run -n verireturn python -m scripts.verify_recovery_evidence
python - <<'PY'
import gzip, json
from pathlib import Path
rows = [json.loads(line) for line in gzip.decompress(Path('docs/recovery-evidence/formal-v2.results.jsonl.gz').read_bytes()).decode().splitlines()]
for strategy in ['react_baseline', 'recovery_agent']:
    group = [r for r in rows if r['strategy'] == strategy]
    print(strategy, len(group), sum(r['business_completed'] for r in group))
PY
```

旧核心源码若用于重放，应在独立 checkout 中解压 `protocol-source.zip`；它覆盖交付源码中的核心模型/引擎/判定器/世界/样本文件。完整交付源码另存 `release-source.zip`。正式及旧套件数据本身永久以内容快照保存；后续实现运行旧样本需要明确记录新实现及 Prompt 版本，不能冒充原实验。
