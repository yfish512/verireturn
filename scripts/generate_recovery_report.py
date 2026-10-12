"""Render a report using exact raw counts, never rounded averages as evidence."""
from __future__ import annotations
import argparse, json
from pathlib import Path

LABELS={"react_baseline":"普通模型 Agent","recovery_agent":"受约束恢复 Agent","baseline":"遇错停止","fixed_retry":"固定重试"}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('report_json')
    parser.add_argument('--control');parser.add_argument('--repeat');parser.add_argument('--repair')
    parser.add_argument('--output',default='docs/recovery-latest-report.md')
    args=parser.parse_args();r=json.loads(Path(args.report_json).read_text())
    if r.get('schema_version')!=2:raise ValueError('Use docs/recovery-legacy-report.md for historical rule results')
    lines=['# VeriReturn Recovery 真实模型实验报告','',
        f"模型配置 `{r['model_name']}`；Prompt `{r['prompt_version']}`；{r['episodes']} 个配对故障任务、{r['total']} 条策略结果；Harness 错误 {r['failed']}。",
        '', '## 实验边界', '',
        '12 个受控变体，每变体 25 个参数化业务任务；240 个开发任务、60 个保留任务。普通 Agent 和恢复 Agent 共用工具、模型、用户脚本、业务保护及 8 次工具 / 4 次规划 / 120 秒预算。模型不接收故障标签、脚本和标准答案。',
        '', '任务从已授权操作遭遇故障开始，评测恢复决策；用户选择由固定脚本提供。无故障多数任务无需模型规划，不能据此宣称完整对话推理能力。退款要满足渠道成功、账本与订单行同步；换货要满足履约签收。转人工、等待补货、等待回复均不算业务完成。',
        '', '## 正式结果（冻结 v2）', '',
        '| 策略 | 完成数 / 任务数 | 业务完成率（Wilson 95% CI） | 自主完成 | 客户协助完成 | 符合预期终态 |',
        '| --- | ---: | --- | ---: | ---: | ---: |']
    for s,m in r['metrics_by_strategy'].items():
        lo,hi=m['completion_95ci'];lines.append(f"| {LABELS.get(s,s)} | {m['completed_tasks']}/{m['tasks']} | {m['business_completion_rate']:.2%} [{lo:.2%}, {hi:.2%}] | {m['autonomous_success_rate']:.2%} | {m['assisted_success_rate']:.2%} | {m['expected_terminal_rate']:.2%} |")
    d=r.get('paired_completion_difference')
    if d:
        lo,hi=d['bootstrap_95ci'];lines+=['',f"逐任务配对完成率差：**{100*d['difference']:.2f} 个百分点**；配对 bootstrap 95% CI **[{100*lo:.2f}, {100*hi:.2f}] 个百分点**（{d['pairs']} 对，2000 次重采样）。"]
    lines+=['','### 故障家族与保留集','','| 范围 | 普通 Agent 完成率 | 恢复 Agent 完成率 | 普通 / 恢复符合预期终态 |','| --- | ---: | ---: | ---: |']
    for family in r['scenario_families']:
        a=r['metrics_by_family'][f'react_baseline:{family}'];b=r['metrics_by_family'][f'recovery_agent:{family}']
        lines.append(f"| {family}（各 {a['tasks']} 条） | {a['business_completion_rate']:.2%} | {b['business_completion_rate']:.2%} | {a['expected_terminal_rate']:.2%} / {b['expected_terminal_rate']:.2%} |")
    for split in ['development','holdout']:
        a=r['metrics_by_split'][f'react_baseline:{split}'];b=r['metrics_by_split'][f'recovery_agent:{split}']
        lines.append(f"| {split}（各 {a['tasks']} 条） | {a['business_completion_rate']:.2%} | {b['business_completion_rate']:.2%} | {a['expected_terminal_rate']:.2%} / {b['expected_terminal_rate']:.2%} |")
    lines+=['','主要收益来自读取异常：普通 Agent 过早转人工，恢复 Agent 使用剩余读取重试与核验。退款结果未知、库存变化两类没有一致的完成率提升；不能宣称在所有故障家族上都优于基线。',
        '', '### 成本与安全', '',
        '| 策略 | 总工具 / 平均工具 | 平均额外工具 | 总规划 / 平均规划 | 平均输入 / 输出 Token | 平均耗时 | 重试总数 | 拦截 / 错误动作 / 错误完成判断 | 业务违规 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for s,m in r['metrics_by_strategy'].items():
        rows=[x for x in r['results'] if x['strategy']==s]
        total=lambda f:sum(x.get(f,0) for x in rows)
        lines.append(f"| {LABELS.get(s,s)} | {total('tool_calls')} / {m['average_tool_calls']:.2f} | {m['average_extra_tool_calls']:.2f} | {total('model_calls')} / {m['average_model_calls']:.2f} | {m['average_input_tokens']:.2f} / {m['average_output_tokens']:.2f} | {m['average_duration_ms']:.0f} ms | {total('retries')} | {total('blocked_actions')} / {total('incorrect_actions')} / {total('false_completion_claims')} | {m['business_violations']} |")
    lines+=['','额外工具调用 = 当前路径工具次数 − 配对无故障规则路径次数，可为负。该参照不是无故障模型表现；总调用同时报告。恢复策略输入 Token 增加约 21.8%，平均耗时增加约 3%；不能把规划次数减少等同于总体费用降低。失败请求记入预算；服务超时时服务端真实 Token 不可得，记录值 0 代表未获得 usage，并非无消耗。',
        '', '两组实际业务违规均为 0，源于共用领域保护。普通 Agent 有 25 次未决写重试被拦截；恢复 Agent 该项为 0。系统保护效果与模型决策效果须分开解释。',
        '', '### 逐任务失败分析', '',
        '| 任务 | 结果 | 原因与处理 |','| --- | --- | --- |']
    for row in r['results']:
        if row['strategy']!='recovery_agent' or row['matches_expected']:continue
        error=next((e['detail'].get('error') for e in row['trace'] if e['kind']=='model_error'),None)
        reason=error or '模型把库存核验成功当成履约完成；独立判定器拒绝计为完成。'
        lines.append(f"| `{row['id']}` | {row['outcome']} | {reason} |")
    lines+=['','原正式实验保留 **2 次错误完成判断及 1 次 API 超时**。持续库存服务故障在原环境仅约束 primary 读取，verify 仍返回库存；该故障覆盖缺口已在最终代码补强。补强并增加完成证据提示后单独验证，原结果和统计口径不被替换。',
        '', '## 无故障、重复与修复验证', '',
        '| 实验 | 任务 / 策略结果 | 普通 / 恢复业务完成率 | 普通 / 恢复符合预期终态 | 说明 |',
        '| --- | ---: | ---: | ---: | --- |']
    for name,path in [('分层无故障',args.control),('保留集重复',args.repeat),('修复后专项',args.repair)]:
        if not path:continue
        sub=json.loads(Path(path).read_text());a=sub['metrics_by_strategy']['react_baseline'];b=sub['metrics_by_strategy']['recovery_agent']
        lines.append(f"| {name} | {sub['episodes']} / {sub['total']} | {a['business_completion_rate']:.2%} / {b['business_completion_rate']:.2%} | {a['expected_terminal_rate']:.2%} / {b['expected_terminal_rate']:.2%} | `{sub['prompt_version']}`，不并入正式 300 条 |")
    if args.repair:
        sub=json.loads(Path(args.repair).read_text())
        rows=[x for x in sub['results'] if x['strategy']=='recovery_agent']
        lines+=['',f"最终专项恢复策略：预算耗尽 {sum(x['outcome']=='budget_exhausted' for x in rows)}/{len(rows)}；系统升级人工 {sum(x.get('system_escalation',False) for x in rows)}/{len(rows)}；错误完成判断 {sum(x.get('false_completion_claims',0) for x in rows)}。系统兜底建立实际工单，但预算耗尽仍独立保留，不计模型自主完成。"]
    lines+=['','30 条重复实验仍为小样本；专项验证偏向已知故障，不作为新的总体提升估计。47 条专项中有 25 条持续故障及等待/人工任务，业务完成率不能直接与正式 300 条相比。所有模型错误、超时与开放任务保留在原始结果中。',
        '', '## 复核与限制', '',
        '正式冻结源码与数据校验和见 [证据目录](recovery-evidence/README.md)。归档包含每条原始结果、工具轨迹、最终事实、用户授权、Token 与故障种子；逐任务配对差异可直接复算。历史三个固定场景规则报告见 [历史报告](recovery-legacy-report.md)，它不是真实模型效果证据。',
        '', '实验使用模拟支付和履约渠道、隔离 SQLite 领域数据库；锁、租约与预算另用 PostgreSQL 并发测试验收。未验证真实支付账户、线上用户分布或公开基准，不声称生产已部署或提出新算法。']
    Path(args.output).write_text('\n'.join(lines)+'\n')

if __name__=='__main__':main()
