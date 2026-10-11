"""Render a compact, reviewable Recovery experiment report from evaluator JSON."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("report_json"); parser.add_argument("--output", default="docs/recovery-latest-report.md")
    args = parser.parse_args(); report = json.loads(Path(args.report_json).read_text(encoding="utf-8"))
    lines = ["# VeriReturn Recovery 实验报告", "", f"- Planner：`{report['planner_mode']}`", f"- 实验任务：{report['total']} 个策略任务", f"- Harness 错误：{report['failed']}", "", "| 策略 | 自主成功率 | 安全转人工率 | 严重错误率 | 等待二次确认率 | 平均额外工具调用 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for strategy, metric in report["metrics_by_strategy"].items():
        lines.append(f"| {strategy} | {metric['autonomous_success_rate']:.2%} | {metric['safe_handoff_rate']:.2%} | {metric['unsafe_failure_rate']:.2%} | {metric['awaiting_reconfirmation_rate']:.2%} | {metric['average_extra_tool_calls']:.2f} |")
    lines += ["", "结果来自固定种子、隔离数据库 fixture 和相同领域保护机制。`awaiting_reconfirmation` 表示 Agent 发现库存等业务事实变化后停止原命令，向用户征求新的明确确认；它不计入自主完成。"]
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True); output.write_text("\n".join(lines) + "\n", encoding="utf-8")
if __name__ == "__main__": main()
