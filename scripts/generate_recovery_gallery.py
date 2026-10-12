"""Create an offline casebook and PNG gallery from frozen real-model traces.

No API, database or model calls. All selections are validated against SHA256-
checked archives. These are evidence illustrations, not application screenshots.
"""
from __future__ import annotations

import gzip
import hashlib
import html
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/recovery-evidence"
OUT = ROOT / "docs/images/recovery-gallery"
STRATEGIES = ("react_baseline", "recovery_agent")
# Freeze the selections, including failures; regeneration cannot select better runs.
CASES = [
    ("formal-v2", "read_once-001:r0", "一次读取失败", "读取异常", "两组均重试成功，展示基础恢复。"),
    ("formal-v2", "read_twice-001:r0", "连续两次读取失败", "读取异常", "普通模型转人工；恢复模型在预算内再次读取。"),
    ("formal-v2", "read_persistent-001:r0", "持续不可用", "读取异常", "两组均转人工，未将开放任务计为完成。"),
    ("formal-v2", "read_verification-001:r0", "核验过程中再次失败", "读取异常", "恢复模型再次核验，普通模型提前转人工。"),
    ("formal-v2", "write_before_commit-001:r0", "提交前失败", "退款结果未知", "取得未执行证据后重试，再核对渠道结果。"),
    ("formal-v2", "write_remote_only-001:r0", "渠道接受，本地未回写", "退款结果未知", "先核验渠道事实，避免盲目重新提交。"),
    ("formal-v2", "write_response_lost-001:r0", "执行成功，响应丢失", "退款结果未知", "查询已有执行结果，最终核对账本与渠道。"),
    ("formal-v2", "write_query_unavailable-001:r0", "状态查询暂时不可用", "退款结果未知", "核验失败后继续查询，保持写入结果未决。"),
    ("formal-v2", "stock_restored-001:r0", "原 SKU 恢复库存", "确认期间状态变化", "刷新业务状态并重新获得客户确认。"),
    ("formal-v2", "stock_alternative-001:r0", "选择替换 SKU", "确认期间状态变化", "客户确认允许的替换方案后完成履约。"),
    ("formal-v2", "stock_refund-001:r0", "换货改为退款", "确认期间状态变化", "新授权关联退款单，按退款事实判定完成。"),
    ("formal-v2", "stock_wait_human-001:r0", "客户等待补货", "确认期间状态变化", "保持开放任务；等待补货不等于业务完成。"),
    ("formal-v2", "stock_wait_human-002:r0", "客户选择人工处理", "客户分支", "工单已创建，单独统计人工处理。"),
    ("formal-v2", "stock_wait_human-003:r0", "客户拒绝新方案", "客户分支", "确认被拒绝，不擅自执行退款或换货。"),
    ("formal-v2", "stock_wait_human-004:r0", "客户尚未回复", "客户分支", "保留待确认状态，不把沉默当成授权。"),
    ("formal-v2", "stock_wait_human-005:r0", "客户撤回任务", "客户分支", "恢复任务取消，未完成业务。"),
    ("formal-v2", "read_persistent-011:r0", "模型错误判断已完成", "失败案例", "核验库存不等于完成换货；独立判定器拒绝计分。"),
    ("formal-v2", "write_query_unavailable-018:r0", "模型服务超时", "失败案例", "规划 API 超时显式记录，未切回规则冒充模型成功。"),
    ("repair-v3-final", "read_persistent-005:r0", "v3：持续故障转人工", "修复专项", "模型选择转人工；专项结果不替换正式效果统计。"),
    ("repair-v3-final", "read_persistent-001:r0", "v3：预算耗尽兜底", "修复专项", "系统创建工单，不能计为模型自主恢复。"),
]
NAMES = {"react_baseline": "普通模型 Agent", "recovery_agent": "受约束恢复 Agent"}
OUTCOMES = {
    "autonomous_success": "自主完成", "assisted_success": "客户协助完成",
    "safe_handoff": "转人工", "waiting_stock": "等待补货",
    "awaiting_reconfirmation": "等待客户确认", "cancelled": "已取消",
    "safe_failure": "未完成 / 判定不通过", "model_error": "模型服务异常",
    "budget_exhausted": "预算耗尽 / 系统兜底",
}
LABELS = {
    "pending_confirmation": "待确认", "completed": "已完成", "fulfillment_exception": "履约异常",
    "succeeded": "成功", "submitted": "已提交", "delivered": "已签收",
    "cancelled": "已取消", "refund": "退款", "exchange": "换货", "wait": "等待补货",
    "human": "人工处理", "applied": "方案已执行", "waiting_stock": "等待补货",
    "rejected": "方案被拒绝", "proposed": "方案待确认",
    "primary": "执行原操作", "retry_tool": "重试原操作",
    "verify_execution_status": "核验执行状态", "wait_for_result": "查询渠道结果",
    "refresh_business_state": "刷新业务状态", "offer_alternative": "提出新方案并确认",
    "handoff_to_human": "转人工", "finish": "判断任务完成",
}


def esc(value):
    return html.escape(str(value), quote=True)


def label(value):
    return LABELS.get(value, str(value)) if value is not None else "未创建"


def load_archives():
    manifest = json.loads((EVIDENCE / "SHA256.json").read_text())
    archives = {}
    for source in sorted({case[0] for case in CASES}):
        name = f"{source}.results.jsonl.gz"
        path = EVIDENCE / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest[name], name
        with gzip.open(path, "rt") as stream:
            rows = [json.loads(line) for line in stream]
        assert len(rows) == (600 if source == "formal-v2" else 94)
        archives[source] = rows
    selections = []
    for index, (source, episode, title, family, note) in enumerate(CASES, 1):
        pair = [r for r in archives[source] if r["episode_id"] == episode]
        assert len(pair) == 2 and {r["strategy"] for r in pair} == set(STRATEGIES), episode
        assert len({r["variant"] for r in pair}) == 1
        assert all(not r["business_violations"] for r in pair)
        for row in pair:
            f = row["final_state"]
            assert f["refunded_quantity"] <= f["quantity"]
            assert all(s["available"] >= s["reserved"] >= 0 for s in f["stocks"])
            if row["business_completed"]:
                assert f["case_status"] == "completed"
                if f["request_type"] == "refund":
                    assert f["refund_status"] == f["channel"]["status"] == "succeeded"
                    assert f["refund_amount"] == f["channel"]["amount"]
                    assert f["refunded_quantity"] == f["quantity"]
                else:
                    assert f["exchange_status"] == "delivered"
        recovery = next(r for r in pair if r["strategy"] == "recovery_agent")
        if index <= 12:
            assert recovery["variant"] == episode.split("-")[0]
        selections.append(dict(index=index, source=source, episode=episode, title=title,
                               family=family, note=note, rows=pair,
                               image=f"{index:02d}-{episode.split(':')[0]}-{source}.png"))
    assert len({c["rows"][0]["variant"] for c in selections[:12]}) == 12
    assert len({(c["source"], c["episode"]) for c in selections}) == 20
    return selections


def tone(row):
    if not row["matches_expected"] or row["outcome"] == "model_error":
        return "bad"
    return "good" if row["business_completed"] else "open"


def facts(row):
    f = row["final_state"]
    result = [("售后单", f"#{f['case_id']} · {label(f['case_status'])}"),
              ("售后类型 / 数量", f"{label(f['request_type'])} · {f['quantity']} 件")]
    if f["request_type"] == "refund":
        channel = f.get("channel") or {}
        result += [("退款本地 / 渠道", f"{label(f.get('refund_status'))} / {label(channel.get('status'))}"),
                   ("金额本地 / 渠道", f"{f.get('refund_amount') or '—'} / {channel.get('amount') or '—'} 元"),
                   ("已退款数量", f"{f['refunded_quantity']} / {f['quantity']} 件"),
                   ("退款尝试次数", f["refund_attempts"])]
    else:
        result.append(("换货履约", label(f.get("exchange_status"))))
    result += [("库存预占合计", sum(s["reserved"] for s in f["stocks"])),
               ("人工工单", "已创建" if f["handoff_created"] else "未创建")]
    if f["resolutions"]:
        result.append(("客户方案事实", "；".join(f"{label(p['option'])} · {label(p['status'])}" for p in f["resolutions"])))
    return "".join(f'<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>' for k, v in result)


def timeline(row):
    pieces = []
    kinds = {"observation": "工具反馈", "decision": "模型决策", "model_error": "模型异常",
             "budget": "预算停止", "system_escalation": "系统兜底", "completion_claim": "模型完成判断"}
    for step in row["trace"]:
        kind = step["kind"]
        if kind not in kinds:
            continue
        detail = step["detail"]
        if kind == "decision":
            body = detail.get("reason") or label(detail.get("action"))
        elif kind == "observation":
            result = detail.get("result") or {}
            body = result.get("message")
            if not body:
                # Preserve short status facts verbatim; exclude long provider references.
                values = {k: v for k, v in result.items() if k in ("status", "terminal", "error", "available", "version", "local_status", "case_id", "options", "authoritative")}
                body = json.dumps(values, ensure_ascii=False) if values else label(detail.get("action"))
        elif kind == "completion_claim":
            body = "模型选择结束；是否完成仍以独立业务判定为准。"
        else:
            body = detail.get("error") or detail.get("reason") or detail.get("scope") or kind
        pieces.append(f'<li><b>{step["sequence_no"]:02d} · {kinds[kind]}</b><p>{esc(body)}</p></li>')
    return "".join(pieces)


def strategy_panel(row):
    tag = OUTCOMES.get(row["outcome"], row["outcome"])
    completed = "完整完成" if row["business_completed"] else "未完整完成"
    return f'''<article class="panel {tone(row)}" data-strategy="{esc(row['strategy'])}" data-episode="{esc(row['episode_id'])}">
      <header><h2>{NAMES[row['strategy']]}</h2><span class="badge">{esc(tag)}</span></header>
      <div class="verdict"><b>{completed}</b><span>预期终态：{'符合' if row['matches_expected'] else '不符合'}</span></div>
      <div class="cost"><span>工具 <b>{row['tool_calls']}</b></span><span>模型请求 <b>{row['model_calls']}</b></span><span>耗时 <b>{row['duration_ms']/1000:.2f}s</b></span><span>输入 / 输出 Token <b>{row['input_tokens']} / {row['output_tokens']}</b></span></div>
      <h3>最终业务事实</h3><dl>{facts(row)}</dl>
      <div class="guard">错误完成判断 {row.get('false_completion_claims',0)} · 被拦截动作 {row.get('blocked_actions',0)} · 实际业务违规 {len(row['business_violations'])}</div>
      <h3>执行轨迹 · 保留原始顺序与模型理由</h3><ol>{timeline(row)}</ol>
      </article>'''


CSS = '''
*{box-sizing:border-box}body{margin:0;background:#f4f7fb;color:#172b43;font-family:"Noto Sans CJK SC","Noto Sans CJK JP",sans-serif;font-size:16px}
main{max-width:1500px;margin:auto;padding:44px 48px}h1{font-size:36px;margin:0 0 14px}h2{font-size:22px;margin:0}h3{font-size:17px;margin:22px 0 12px}p{line-height:1.6;margin:8px 0}.subtitle,.meta{color:#586d83}.meta{font-size:14px}.legend{margin:24px 0 28px;color:#586d83;font-size:14px}.legend strong{color:#172b43}
button,a{font:inherit}button{cursor:pointer;border:1px solid #dfe6ef;border-radius:10px;background:white;color:#172b43;padding:8px 18px}.filters{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0}.filters button.active{background:#172b43;color:white}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:18px}.tile{background:white;border:1px solid #dfe6ef;border-radius:16px;padding:20px;text-align:left;min-height:224px}.tile:hover{border-color:#008979}.tile h2{font-size:20px;line-height:1.5;margin:10px 0}.tile p{font-size:14px;color:#586d83;min-height:46px}.number{color:#008979;font-weight:bold;font-size:14px}.small{font-size:13px;color:#586d83}.results{margin-top:16px;display:flex;flex-direction:column;gap:8px;font-size:14px}.good .status{color:#008979}.bad .status{color:#b23f4d}.open .status{color:#9b6818}.results span{display:flex;justify-content:space-between;gap:4px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:28px}.panel{background:white;border:1px solid #dfe6ef;border-top:5px solid #5475d1;border-radius:16px;padding:24px}.panel.good{border-top-color:#008979}.panel.bad{border-top-color:#b23f4d}.panel.open{border-top-color:#b77b2b}.panel header{display:flex;flex-wrap:wrap;justify-content:space-between;gap:16px;align-items:start}.badge{font-size:13px;padding:4px 8px;background:#f4f7fb;border-radius:5px;white-space:nowrap}.verdict{display:flex;justify-content:space-between;padding:14px 0;margin-top:12px;border-bottom:1px solid #e8edf3}.verdict span{font-size:14px;color:#586d83}.cost{display:flex;gap:10px 24px;flex-wrap:wrap;margin-top:16px;color:#586d83;font-size:14px}.cost b{color:#172b43}dl{margin:0;background:#f4f7fb;padding:14px;border-radius:10px}dl div{display:flex;font-size:14px;line-height:1.65;margin:4px 0}dt{width:140px;flex-shrink:0;color:#586d83}dd{margin:0;overflow-wrap:anywhere}.guard{font-size:13px;color:#586d83;margin-top:14px}ol{list-style:none;margin:0;padding:0}li{border-left:2px solid #d6e4eb;padding:0 0 18px 18px;position:relative}li:before{position:absolute;content:"";width:8px;height:8px;border-radius:50%;left:-5px;top:7px;background:#008979}li b{font-size:14px}li p{font-size:15px;color:#586d83;overflow-wrap:anywhere;margin:6px 0 0}.notice{background:#e9f1fa;border-left:4px solid #5475d1;padding:14px 18px;margin:18px 0;font-size:15px}.footer{font-size:13px;color:#586d83;margin:24px 0 0;line-height:1.7}.back{margin-bottom:22px}.detail[hidden],.tile[hidden],#overview[hidden]{display:none}
@media(max-width:900px){main{padding:24px}.grid{grid-template-columns:repeat(2,1fr)}.pair{grid-template-columns:1fr}h1{font-size:28px}}@media(max-width:600px){.grid{grid-template-columns:1fr}.verdict{flex-wrap:wrap;gap:8px}dt{width:110px}}
'''


def document(cases):
    tiles, details = [], []
    for case in cases:
        idx = case["index"]
        by_strategy = {r["strategy"]: r for r in case["rows"]}
        outcomes = "".join(f'<span class="{tone(by_strategy[s])}">{"普通模型" if s == "react_baseline" else "恢复模型"}<b class="status">{esc(OUTCOMES.get(by_strategy[s]["outcome"], by_strategy[s]["outcome"]))}</b></span>' for s in STRATEGIES)
        version = "v2 正式" if case["source"] == "formal-v2" else "v3 专项"
        tiles.append(f'''<button class="tile" data-family="{esc(case['family'])}" onclick="showCase({idx})"><div class="number">{idx:02d} / {esc(case['family'])}</div><h2>{esc(case['title'])}</h2><p>{esc(case['note'])}</p><div class="small">{version} · {esc(case['episode'])}</div><div class="results">{outcomes}</div></button>''')
        details.append(f'''<section class="detail" id="case-{idx}" hidden><button class="back" onclick="showOverview()">← 返回案例目录</button><div class="number">VeriReturn Recovery / 案例 {idx:02d} / {version}</div><h1>{esc(case['title'])}</h1><p class="subtitle">{esc(case['note'])}</p><p class="meta">{esc(case['episode'])} · deepseek-v4-flash · 同一业务保护与调用预算</p><div class="pair">{''.join(strategy_panel(by_strategy[s]) for s in STRATEGIES)}</div><p class="footer">数据来源：docs/recovery-evidence/{case['source']}.results.jsonl.gz<br>归档执行证据的可视化，不是产品页面截图；案例用于说明过程，不重新估计成功率。模拟支付与履约；工具反馈为原文，金额与结果由最终业务事实判定；工具反馈文本按原文保留，状态字段节选。</p></section>''')
    families = list(dict.fromkeys(c["family"] for c in cases))
    buttons = '<button class="active" onclick="filterCases(this,\'全部\')">全部 20 组</button>'
    buttons += "".join(f'<button onclick="filterCases(this,\'{esc(f)}\')">{esc(f)}</button>' for f in families)
    script = '''function showCase(n){document.getElementById('overview').hidden=true;document.querySelectorAll('.detail').forEach(e=>e.hidden=true);document.getElementById('case-'+n).hidden=false;window.scrollTo(0,0)}function showOverview(){document.querySelectorAll('.detail').forEach(e=>e.hidden=true);document.getElementById('overview').hidden=false;window.scrollTo(0,0)}function filterCases(button,family){document.querySelectorAll('.filters button').forEach(b=>b.classList.remove('active'));button.classList.add('active');document.querySelectorAll('.tile').forEach(e=>e.hidden=family!=='全部'&&e.dataset.family!==family)}'''
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VeriReturn · 恢复案例集</title><style>{CSS}</style><body><main><section id="overview"><h1>VeriReturn Recovery · 20 组真实案例</h1><p class="subtitle">12 个故障变体 + 客户选择与撤回 + 失败案例 + v3 修复专项</p><div class="notice">40 条真实模型执行轨迹；来自现有 300 条正式配对任务及 47 条修复专项。不会产生新的模型请求。</div><div class="legend"><strong>绿色：完整完成</strong> · 棕色：合法开放 / 人工处理 · 红色：未达预期或模型异常<br>第 01–18 组为冻结 v2；第 19–20 组为 v3 专项。案例说明过程，不替代全量统计。</div><div class="filters">{buttons}</div><div class="grid">{''.join(tiles)}</div><p class="footer">点击案例查看两组实际模型理由、工具反馈、最终业务事实和调用成本。页面为可离线打开的归档证据展示，不是正在运行的客户售后页面。</p></section>{''.join(details)}</main><script>{script}</script></body></html>'''


def markdown(cases):
    lines = ["# VeriReturn Recovery 案例集", "",
             "20 组案例、40 条真实模型轨迹。覆盖正式评测的全部 12 个故障变体，并展示客户分支、失败结果与 v3 修复专项。图片从归档证据生成，不是产品页面截图；不调用模型，不修改历史统计。", "",
             "[离线交互案例集](images/recovery-gallery/casebook.html)：下载后用浏览器打开，可按类型筛选并查看逐步对照。", "",
             "![案例总览](images/recovery-gallery/overview.png)", "",
             "| 案例 | 故障或客户选择 | 数据版本 | 普通模型 | 恢复模型 |", "| --- | --- | --- | --- | --- |"]
    for case in cases:
        by_strategy = {r["strategy"]: r for r in case["rows"]}
        outcomes = " | ".join(OUTCOMES.get(by_strategy[s]["outcome"], by_strategy[s]["outcome"]) for s in STRATEGIES)
        version = "v2 正式" if case["source"] == "formal-v2" else "v3 专项"
        lines.append(f"| [案例 {case['index']:02d}](#案例-{case['index']:02d}) | {case['title']} | {version} | {outcomes} |")
    for case in cases:
        lines += ["", f"## 案例 {case['index']:02d}", "", f"**{case['title']}** — {case['note']}", "",
                  f"来源：`{case['source']}` · `{case['episode']}`。", "",
                  f"![{case['title']}](images/recovery-gallery/{case['image']})"]
    lines += ["", "## 数据与生成", "",
              "案例选取固定在生成脚本中，第 01–12 组取每个变体的第一条，第 13–16 组覆盖不同客户分支，第 17–18 组明确保留模型误判与服务超时，第 19–20 组区分模型转人工与系统预算兜底。不是随机抽样，也不能用这些案例重新计算成功率。", "",
              "v2 的持续故障脚本只约束原始读取，核验工具仍可能读到库存；该环境局限在第 17 组也有体现。v3 修复专项补强了这一点，与正式实验独立展示。开放任务、转人工、模型异常和预算停止均不计完整业务完成。", "",
              "```sh", "conda activate verireturn", "python -m scripts.verify_recovery_evidence", "python -m scripts.generate_recovery_gallery", "```", "",
              "依赖已安装的 Playwright Chromium；已有浏览器可用 `PLAYWRIGHT_CHROMIUM_PATH` 指定路径。不需要 API、数据库或密钥。生成器校验源文件 SHA256、配对身份、故障覆盖及最终业务事实；浏览器验证筛选、案例切换和无横向溢出。", "",
              "[全部实测统计](recovery-latest-report.md) · [案例来源与图片清单](images/recovery-gallery/manifest.json) · [项目成果展示](showcase.md)", ""]
    return "\n".join(lines)


def main():
    cases = load_archives()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "casebook.html").write_text(document(cases))
    (ROOT / "docs/recovery-cases.md").write_text(markdown(cases))
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_PATH") or p.chromium.executable_path,
                                    headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto((OUT / "casebook.html").as_uri())
        page.evaluate("document.fonts.ready")
        assert page.locator(".tile:visible").count() == 20
        page.get_by_role("button", name="失败案例", exact=True).click()
        assert page.locator(".tile:visible").count() == 2
        page.get_by_role("button", name="全部 20 组", exact=True).click()
        assert page.locator(".tile:visible").count() == 20
        page.screenshot(path=str(OUT / "overview.png"), full_page=True, animations="disabled")
        for case in cases:
            page.locator(".tile").nth(case["index"] - 1).click()
            assert page.locator(".panel:visible").count() == 2
            assert all(page.locator(f"#case-{case['index']} .panel[data-strategy='{strategy}']").get_attribute("data-episode") == case["episode"] for strategy in STRATEGIES)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(OUT / case["image"]), full_page=True, animations="disabled")
            page.get_by_role("button", name="← 返回案例目录", exact=True).click()
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.locator(".tile").nth(19).click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors, errors
        browser.close()
    manifest = dict(cases=[{k: v for k, v in c.items() if k != "rows"} for c in cases],
                    source_hashes={name: hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest()
                                   for name in ("formal-v2.results.jsonl.gz", "repair-v3-final.results.jsonl.gz")},
                    browser_errors=errors, model_requests=0, illustration_type="archived_evidence")
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(dict(cases=len(cases), paired_traces=40, screenshots=21, model_requests=0, browser_errors=errors), ensure_ascii=False))


if __name__ == "__main__":
    main()
