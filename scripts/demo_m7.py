"""Run the M7 portfolio flow only through the public HTTP interfaces.

The result is persisted under .local/ so a presenter can rerun the command
without manufacturing a second business outcome.  Set M7_RUN_KEY to create a
separate, explicitly named replay of the same controlled fixture scenario.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic_settings import BaseSettings, SettingsConfigDict

from scripts.seed_m7_scenario import DEMO_USER_ID, REFUND_ORDER_ID, REVIEW_ORDER_ID


ROOT = Path(__file__).resolve().parents[1]
RESULT_DIR = ROOT / ".local" / "m7"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    fulfillment_webhook_secret: str


class DemoError(RuntimeError):
    pass


def api_url() -> str:
    return os.getenv("VERIRETURN_API_URL", "http://127.0.0.1:8000").rstrip("/")


def request(method: str, path: str, body: dict | None = None, *, actor: str | None = None, headers: dict[str, str] | None = None):
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode() if body is not None else None
    request_headers = {"Accept": "application/json"}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    if actor:
        request_headers["X-Demo-User-Id"] = actor
    request_headers.update(headers or {})
    target = api_url() + path
    try:
        with urlopen(Request(target, data=payload, method=method, headers=request_headers), timeout=30) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1200]
        raise DemoError(f"{method} {path} -> HTTP {error.code}: {detail}") from error
    return json.loads(raw) if raw else None


def run_worker(name: str) -> None:
    environment = os.environ.copy()
    environment.setdefault("DATABASE_URL", "postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn")
    subprocess.run([sys.executable, "-m", f"scripts.run_{name}_worker"], cwd=ROOT, env=environment, check=True, timeout=120)


def signed_webhook(case_id: int, event_type: str, sequence_no: int, run_key: str) -> dict:
    payload = {
        "event_id": f"m7-{run_key}-{case_id}-{sequence_no}",
        "case_id": case_id,
        "event_type": event_type,
        "sequence_no": sequence_no,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "payload": {"scenario": "M7-DEMO", "source": "signed-demo-provider"},
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(Settings().fulfillment_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Provider-Signature": signature,
        "X-Provider-Timestamp": datetime.now(timezone.utc).isoformat(),
        "X-Provider-Event-Id": payload["event_id"],
    }
    return request("POST", "/internal/fulfillment/webhooks/demo_fulfillment", payload, headers=headers)


def result_path(run_key: str) -> Path:
    return RESULT_DIR / f"demo-{run_key}.json"


def load_completed_result(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        case = request("GET", f"/tools/after-sales/cases/{saved['refund_case_id']}", actor=DEMO_USER_ID)
        if case["status"] == "completed":
            return saved
    except (KeyError, ValueError, DemoError):
        return None
    return None


def main() -> None:
    run_key = os.getenv("M7_RUN_KEY", "portfolio-v1")
    path = result_path(run_key)
    saved = load_completed_result(path)
    if saved is not None:
        print(json.dumps({"reused_existing_demo": True, **saved}, ensure_ascii=False, indent=2))
        return

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    request("GET", "/health")

    # A. Evidence-only answer: the Agent invokes the M4 retrieval path and does not write business data.
    knowledge = request("POST", f"/agent/threads/m7-knowledge-{run_key}/messages", {
        "message": "退款政策和上门取件流程是什么？", "message_id": f"m7-knowledge-{run_key}",
    }, actor=DEMO_USER_ID)

    # B. A customer asks for an eligible refund.  Creation halts at the persisted confirmation boundary.
    refund = request("POST", f"/agent/threads/m7-refund-{run_key}/messages", {
        "message": f"帮我退 {REFUND_ORDER_ID}，商品未拆封", "message_id": f"m7-refund-create-{run_key}",
    }, actor=DEMO_USER_ID)
    if refund["status"] != "awaiting_confirmation" or not refund["confirmation_id"] or not refund["case_id"]:
        raise DemoError(f"expected pending confirmation for refund, got {refund}")
    pending_case = request("GET", f"/tools/after-sales/cases/{refund['case_id']}", actor=DEMO_USER_ID)
    if pending_case["status"] != "pending_confirmation":
        raise DemoError(
            "the selected M7 run key already owns a partially executed case; "
            "use a new M7_RUN_KEY instead of mutating that historical outcome"
        )
    confirmed = request("POST", f"/agent/confirmations/{refund['confirmation_id']}", {"approved": True}, actor=DEMO_USER_ID)
    if confirmed["status"] != "completed":
        raise DemoError(f"refund confirmation did not complete: {confirmed}")
    pickup = request("POST", f"/agent/threads/m7-pickup-{run_key}/messages", {
        "message": f"售后单 #{refund['case_id']} 明天上午取件", "message_id": f"m7-pickup-{run_key}",
    }, actor=DEMO_USER_ID)
    if pickup["status"] != "completed":
        raise DemoError(f"pickup scheduling did not complete: {pickup}")
    run_worker("fulfillment")
    fulfillment_events = [
        signed_webhook(refund["case_id"], "pickup.collected", 1, run_key),
        signed_webhook(refund["case_id"], "return.received", 2, run_key),
        signed_webhook(refund["case_id"], "refund.processing", 3, run_key),
        signed_webhook(refund["case_id"], "refund.completed", 4, run_key),
    ]
    run_worker("fulfillment")
    fulfillment = request("GET", f"/tools/after-sales/cases/{refund['case_id']}/fulfillment", actor=DEMO_USER_ID)

    # C. Quality-dispute refund: Agent only submits a review request after a second customer confirmation.
    review_request = request("POST", f"/agent/threads/m7-review-{run_key}/messages", {
        "message": f"订单 {REVIEW_ORDER_ID} 损坏了，申请退款人工审核", "message_id": f"m7-review-create-{run_key}",
    }, actor=DEMO_USER_ID)
    if review_request["status"] != "awaiting_confirmation" or not review_request["confirmation_id"]:
        raise DemoError(f"expected pending review confirmation, got {review_request}")
    review_submitted = request("POST", f"/agent/confirmations/{review_request['confirmation_id']}", {"approved": True}, actor=DEMO_USER_ID)
    ticket_id = review_submitted.get("ticket_id")
    if not ticket_id:
        raise DemoError(f"review confirmation did not create a ticket: {review_submitted}")
    ticket = request("GET", f"/ops/review-tickets/{ticket_id}", actor="OPS001")
    ticket = request("POST", f"/ops/review-tickets/{ticket_id}/claim", {"expected_version": ticket["version"]}, actor="OPS001", headers={"Idempotency-Key": f"m7-review-claim-{run_key}"})
    ticket = request("POST", f"/ops/review-tickets/{ticket_id}/decisions", {
        "action": "approve_exception", "expected_version": ticket["version"], "reason_code": "M7_QUALITY_EVIDENCE", "customer_message": "演示：质量争议退款已批准，仍需客户确认。",
    }, actor="OPS001", headers={"Idempotency-Key": f"m7-review-decision-{run_key}"})

    # D. Metrics and a deliberately scoped demo-only rule make the M6 alert/trace view non-empty.
    rule = request("POST", "/ops/alert-rules", {
        "rule_key": f"m7-demo-agent-runs-{run_key}", "metric_name": "agent.runs_total", "comparison": ">=", "threshold": 1, "severity": "warning",
    }, actor="OPS_MANAGER", headers={"Idempotency-Key": f"m7-alert-rule-{run_key}"})
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(minutes=10)
    metric_job = request("POST", "/ops/metrics/jobs", {
        "window_start": window_start.isoformat(), "window_end": window_end.isoformat(),
    }, actor="OPS_MANAGER")
    run_worker("metrics")
    alerts = request("GET", "/ops/alerts", actor="OPS001")
    alert = next((item for item in alerts if item["rule_version_id"] == rule["id"]), None)
    if alert is None:
        raise DemoError("M7 demo alert was not created")
    alert = request("POST", f"/ops/alerts/{alert['id']}/acknowledge", {"expected_version": alert["version"]}, actor="OPS001")
    alert = request("POST", f"/ops/alerts/{alert['id']}/resolve", {
        "expected_version": alert["version"], "resolution_note": "M7 演示阈值告警已复核并关闭。",
    }, actor="OPS001")
    trace = request("GET", "/ops/traces?" + urlencode({"run_id": refund["run_id"]}), actor="OPS001")
    evaluation_runs = request("GET", "/ops/evaluation-runs", actor="OPS001")
    evaluation = next((item for item in evaluation_runs if item["status"] == "succeeded"), None)

    result = {
        "scenario": "M7-DEMO", "run_key": run_key, "generated_at": datetime.now(timezone.utc).isoformat(),
        "knowledge_run_id": knowledge["run_id"], "knowledge_retrieval_id": knowledge.get("retrieval_id"), "knowledge_citations": knowledge.get("citations", []),
        "refund_run_id": refund["run_id"], "refund_case_id": refund["case_id"], "refund_confirmation_id": refund["confirmation_id"],
        "pickup_run_id": pickup["run_id"], "review_run_id": review_request["run_id"], "review_ticket_id": ticket_id,
        "review_status": ticket["status"], "fulfillment_status": fulfillment["status"],
        "fulfillment_event_statuses": [item["status"] for item in fulfillment_events],
        "metric_job_id": metric_job["id"], "alert_id": alert["id"], "alert_status": alert["status"],
        "trace": trace, "evaluation_run_id": evaluation["id"] if evaluation else None,
    }
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
