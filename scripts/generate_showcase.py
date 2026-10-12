"""Render public project figures from archived evidence; never calls a model.

Run from the repository root: python -m scripts.generate_showcase
Optional plotting dependencies: scripts/requirements-showcase.txt
"""
from __future__ import annotations

import gzip
import hashlib
import html
import json
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, fontManager
from matplotlib.ticker import PercentFormatter
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/images"
EVIDENCE = ROOT / "docs/recovery-evidence"
BLUE = "#5475D1"
TEAL = "#008979"
INK = "#172B43"
MUTED = "#586D83"
BG = "#F4F7FB"
STRATEGIES = ("react_baseline", "recovery_agent")


def configure_font():
    override = os.getenv("SHOWCASE_FONT_PATH")
    candidates = [override] if override else [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            fontManager.addfont(candidate)
            name = FontProperties(fname=candidate).get_name()
            plt.rcParams["font.family"] = name
            break
    else:
        raise RuntimeError("Install Noto Sans CJK or set SHOWCASE_FONT_PATH to a Chinese font.")
    plt.rcParams.update({
        "font.size": 13, "text.color": INK, "axes.labelcolor": MUTED,
        "xtick.color": MUTED, "ytick.color": MUTED,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.spines.left": False, "axes.spines.bottom": False,
        "axes.unicode_minus": False, "svg.fonttype": "path",
    })


def load_evidence():
    summary_path = EVIDENCE / "formal-v2.summary.json"
    summary = json.loads(summary_path.read_text())
    raw_path = EVIDENCE / "formal-v2.results.jsonl.gz"
    with gzip.open(raw_path, "rt") as stream:
        rows = [json.loads(line) for line in stream]
    manifest = json.loads((EVIDENCE / "SHA256.json").read_text())
    for path in (summary_path, raw_path):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest[path.name]
    assert summary["prompt_version"] == "recovery-prompt-v2"
    assert len(rows) == summary["total"] == 600
    for strategy in STRATEGIES:
        group = [r for r in rows if r["strategy"] == strategy]
        metrics = summary["metrics_by_strategy"][strategy]
        assert len(group) == metrics["tasks"] == 300
        assert sum(r["business_completed"] for r in group) == metrics["completed_tasks"]
        assert all(not r["business_violations"] for r in group)
    return summary, rows


def results_figure(summary, rows):
    configure_font()
    fig = plt.figure(figsize=(16, 11), facecolor=BG)
    fig.text(.055, .945, "VeriReturn Recovery", fontsize=30, weight="bold")
    fig.text(.055, .902, "真实模型对照 · 完整业务完成，而非仅提交工具调用", fontsize=20)
    fig.text(.055, .867, f"冻结 v2 / {summary['model_name']} / 300 条配对故障任务 / 12 个变体 / 相同业务保护与预算", color=MUTED)
    ax = fig.add_axes([.09, .425, .37, .345], facecolor=BG)
    bx = fig.add_axes([.59, .425, .35, .345], facecolor=BG)
    ms = summary["metrics_by_strategy"]
    autonomous, assisted, totals = [], [], []
    for strategy in STRATEGIES:
        m = ms[strategy]
        # Summary rates are rounded; recover exact task counts before plotting.
        autonomous.append(round(m["autonomous_success_rate"] * m["tasks"]) / m["tasks"])
        assisted.append(round(m["assisted_success_rate"] * m["tasks"]) / m["tasks"])
        totals.append(m["completed_tasks"] / m["tasks"])
    assert all(abs(a + b - t) < 1e-8 for a, b, t in zip(autonomous, assisted, totals))
    x = [0, 1]
    ax.bar(x, autonomous, width=.5, color=TEAL, label="自主完成")
    ax.bar(x, assisted, bottom=autonomous, width=.5, color="#ADC7EB", label="客户协助完成")
    errors = [[totals[i] - ms[s]["completion_95ci"][0] for i, s in enumerate(STRATEGIES)],
              [ms[s]["completion_95ci"][1] - totals[i] for i, s in enumerate(STRATEGIES)]]
    ax.errorbar(x, totals, yerr=errors, fmt="none", color=INK, capsize=6, lw=1.5)
    for i, (a, b, t, s) in enumerate(zip(autonomous, assisted, totals, STRATEGIES)):
        ax.text(i, a / 2, f"{a:.2%}", ha="center", va="center", color="white", fontsize=16, weight="bold")
        ax.text(i, a + b / 2, f"{b:.0%}", ha="center", va="center", fontsize=15)
        ax.text(i, min(1.08, ms[s]["completion_95ci"][1] + .04),
                f"{t:.2%}\n{ms[s]['completed_tasks']}/300", ha="center", fontsize=17, weight="bold")
    ax.set_xticks(x, ["普通模型 Agent", "受约束恢复 Agent"])
    ax.set_ylim(0, 1.13)
    ax.set_yticks([0, .25, .5, .75, 1])
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.set_title("完整业务完成率", loc="left", fontsize=18, pad=18, weight="bold")
    ax.grid(axis="y", color="#DFE6EF", zorder=0)
    ax.set_axisbelow(True)
    ax.legend(loc="upper left", bbox_to_anchor=(-.01, -.16), ncol=2, frameon=False, fontsize=12)
    ax.text(0, -.30, "误差线：完成率 Wilson 95% 置信区间", transform=ax.transAxes, color=MUTED, fontsize=11)

    families = ["inventory_temporarily_unavailable", "refund_outcome_unknown", "inventory_changed_during_confirmation"]
    labels = ["读取服务异常", "退款结果未知", "确认期间库存变化"]
    for j, strategy in enumerate(STRATEGIES):
        values = [summary["metrics_by_family"][f"{strategy}:{family}"]["completed_tasks"] / 100 for family in families]
        yy = [i + (.16 if j == 0 else -.16) for i in range(3)]
        bx.barh(yy, values, height=.26, color=BLUE if j == 0 else TEAL,
                label="普通 Agent" if j == 0 else "恢复 Agent")
        for y, v in zip(yy, values):
            bx.text(v + .025, y, f"{v:.0%}", va="center", fontsize=13, weight="bold")
    bx.set_yticks([0, 1, 2], labels)
    bx.invert_yaxis()
    bx.set_xlim(0, 1.16)
    bx.set_xticks([0, .25, .5, .75, 1])
    bx.xaxis.set_major_formatter(PercentFormatter(1))
    bx.grid(axis="x", color="#DFE6EF")
    bx.set_axisbelow(True)
    bx.set_title("故障家族拆分 · 每组各 100 条", loc="left", fontsize=18, pad=18, weight="bold")
    bx.legend(loc="upper left", bbox_to_anchor=(0, -.16), ncol=2, frameon=False, fontsize=12)
    bx.text(0, -.30, "收益集中在读取异常；其余两类没有完成率提升。", transform=bx.transAxes, color=MUTED, fontsize=11)

    pair = summary["paired_completion_difference"]
    fig.text(.055, .258, f"配对提升  +{pair['difference'] * 100:.2f} 个百分点", fontsize=21, weight="bold", color=TEAL)
    ci = pair["bootstrap_95ci"]
    fig.text(.52, .264, f"配对 Bootstrap 95% CI：{ci[0] * 100:.2f}–{ci[1] * 100:.2f} 个百分点", fontsize=13, color=MUTED)
    metrics = [("平均工具调用", "average_tool_calls", ".2f", "次"),
               ("平均模型请求", "average_model_calls", ".2f", "次"),
               ("平均输入 Token", "average_input_tokens", ".0f", ""),
               ("平均单任务耗时", "average_duration_ms", ".2f", "秒")]
    for i, (label, key, precision, unit) in enumerate(metrics):
        left = .055 + .238 * i
        fig.text(left, .201, label, fontsize=12, color=MUTED)
        a, b = ms[STRATEGIES[0]][key], ms[STRATEGIES[1]][key]
        if key == "average_duration_ms":
            a, b = a / 1000, b / 1000
        fig.text(left, .168, f"{a:{precision}} → {b:{precision}} {unit}".strip(), fontsize=18, weight="bold")
    false_claims = sum(r.get("false_completion_claims", 0) for r in rows if r["strategy"] == "recovery_agent")
    # Use archived model_error_rate for count; raw schema may store error under a different field.
    errors = round(ms["recovery_agent"]["model_error_rate"] * 300)
    fig.text(.055, .103, f"输入 Token 增加约 {(ms['recovery_agent']['average_input_tokens'] / ms['react_baseline']['average_input_tokens'] - 1) * 100:.1f}% · 恢复组保留 {false_claims} 次错误完成判断、{errors} 次模型服务异常", fontsize=12, color=MUTED)
    fig.text(.055, .068, "边界：受控参数化任务 + 模拟支付/履约；两组零业务违规来自共用领域约束。转人工、待确认、待补货不计完整完成。", fontsize=11, color=MUTED)
    fig.text(.055, .038, "来源：docs/recovery-evidence/formal-v2.*  |  当前 v3 仅完成 47 条修复专项，未重新运行全量对照。", fontsize=11, color=MUTED)
    fig.savefig(OUT / "recovery-results.png", dpi=150, facecolor=BG)
    svg_path = OUT / "recovery-results.svg"
    fig.savefig(svg_path, facecolor=BG)
    # Matplotlib emits trailing spaces inside SVG path lines; normalize for Git.
    svg_path.write_text("\n".join(line.rstrip() for line in svg_path.read_text().splitlines()) + "\n")
    plt.close(fig)


def architecture_figure():
    nodes = []

    def text(x, y, value, size=22, color=INK, weight=400):
        nodes.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}">{html.escape(value)}</text>')

    def card(x, y, w, h, title, lines, color=TEAL):
        nodes.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="white" stroke="#DFE6EF"/>')
        nodes.append(f'<rect x="{x + 24}" y="{y + 30}" width="5" height="28" rx="2" fill="{color}"/>')
        text(x + 42, y + 54, title, 25, INK, 700)
        for i, line in enumerate(lines):
            text(x + 24, y + 101 + i * 36, line, 20, MUTED)

    text(64, 82, "VeriReturn", 44, "#FFFFFF", 700)
    text(310, 82, "可验证的售后恢复", 34, "#FFFFFF", 500)
    text(64, 137, "模型选择下一步 · 领域规则控制执行 · 最终业务事实证明完成", 24, "#D7E4F2")
    text(64, 206, "React / 会话与任务记忆", 19, MUTED)
    text(450, 206, "LangGraph / DeepSeek", 19, MUTED)
    text(834, 206, "FastAPI / PostgreSQL", 19, MUTED)
    text(1210, 206, "Outbox / Inbox / Worker", 19, MUTED)
    card(64, 236, 340, 230, "01  客户目标与授权", ["商品、数量与期望方案", "多任务记忆与当前焦点", "新方案必须重新确认"])
    card(440, 236, 340, 230, "02  模型恢复规划", ["理解工具观察与执行证据", "选择核验、重试或改方案", "预算内继续，必要时转人工"])
    card(816, 236, 340, 230, "03  领域服务执行", ["权限、金额与数量校验", "事务、行锁、幂等与版本", "库存预占与退款命令"])
    card(1192, 236, 340, 230, "04  渠道与履约事实", ["退款提交与验签回调", "换货出库与签收回写", "独立判定最终业务状态"])
    for x in (404, 780, 1156):
        nodes.append(f'<path d="M {x + 4} 350 H {x + 28}" stroke="{TEAL}" stroke-width="3" marker-end="url(#arrow)"/>')
    nodes.append(f'<path d="M 1362 466 V 519 H 610 V 474" stroke="{TEAL}" stroke-width="3" fill="none" marker-end="url(#arrow)"/>')
    nodes.append(f'<rect x="780" y="494" width="452" height="47" rx="12" fill="{BG}"/>')
    text(798, 524, "工具反馈 → 状态核验 → 再规划", 23, TEAL, 600)
    card(64, 579, 1468, 151, "持久化恢复与执行证据", [
        "任务 / Checkpoint / 未决命令 / 方案确认 / 观察与动作 / 调用成本 / 审计记录",
        "Worker 租约与所有权校验 · 预算原子预留 · 崩溃后按领域事实继续，避免重复执行",
    ], BLUE)
    card(64, 787, 464, 157, "完整完成", ["退款：渠道成功 + 账本 + 数量同步", "换货：履约签收事实回写"], TEAL)
    card(566, 787, 464, 157, "等待客户 / 等待补货", ["方案版本、业务状态与确认有效期", "刷新或重启后恢复开放任务"], BLUE)
    card(1068, 787, 464, 157, "人工处理 / 停止", ["真实工单与可追溯原因", "不把转人工算成自主完成"], "#B77B2B")
    text(64, 1007, "每轮默认上限：8 次工具调用 / 4 次模型规划 / 120 秒；读取最多重试两次。", 20, MUTED)
    text(64, 1046, "展示环境使用模拟支付与履约；故障注入仅限隔离演示数据，生产环境禁用。", 19, MUTED)
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1080" viewBox="0 0 1600 1080">'
    svg += '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="#008979"/></marker></defs>'
    svg += f'<rect width="1600" height="1080" fill="{BG}"/><path d="M0 0 H1600 V165 H0 Z" fill="{INK}"/>'
    svg += '<g font-family="Noto Sans CJK SC, Noto Sans CJK JP, sans-serif">' + "".join(nodes) + '</g></svg>'
    svg_path = OUT / "recovery-flow.svg"
    svg_path.write_text(svg)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_PATH") or p.chromium.executable_path,
            headless=True, args=["--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 1600, "height": 1080}, device_scale_factor=1.5)
        page.goto(svg_path.as_uri())
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(OUT / "recovery-flow.png"), animations="disabled")
        browser.close()


def main():
    OUT.mkdir(exist_ok=True)
    summary, rows = load_evidence()
    results_figure(summary, rows)
    architecture_figure()
    print("Generated docs/images/recovery-{results,flow}.{png,svg}; no model requests.")


if __name__ == "__main__":
    main()
