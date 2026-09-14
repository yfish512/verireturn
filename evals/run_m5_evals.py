"""Evaluate the M5 Agent read boundary against a signed, persisted fulfillment fact."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.models import AfterSalesCase, AgentToolCall, FulfillmentEvent


class EvaluationFailure(AssertionError):
    pass


class M5Evaluator:
    def __init__(self, base_url: str, database_url: str, webhook_secret: str):
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=45)
        self.sessions = sessionmaker(bind=create_engine(database_url))
        self.secret = webhook_secret
        self.prefix = f"m5-eval-{uuid4().hex[:12]}"

    def close(self) -> None:
        self.client.close()

    def _prepare_case(self, row_id: str) -> int:
        headers = {"X-Demo-User-Id": "U001", "Idempotency-Key": f"{self.prefix}-{row_id}-create"}
        created = self.client.post("/tools/after-sales/cases", headers=headers, json={"order_id": "O1001", "request_type": "refund", "reason": "M5 真实模型评测"})
        if created.status_code != 201:
            raise EvaluationFailure(f"cannot create case: {created.text}")
        case_id = created.json()["id"]
        confirmed = self.client.post(f"/tools/after-sales/cases/{case_id}/confirm", headers={"X-Demo-User-Id": "U001", "Idempotency-Key": f"{self.prefix}-{row_id}-confirm"})
        pickup = self.client.post(f"/tools/after-sales/cases/{case_id}/pickup", headers={"X-Demo-User-Id": "U001", "Idempotency-Key": f"{self.prefix}-{row_id}-pickup"}, json={"time_slot": "2026-09-15 上午"})
        if confirmed.status_code != 200 or pickup.status_code != 200:
            raise EvaluationFailure("cannot prepare scheduled pickup")
        event = {"event_id": f"{self.prefix}-{row_id}-pickup-event", "case_id": case_id, "event_type": "pickup.collected", "sequence_no": 1, "occurred_at": datetime.now(timezone.utc).isoformat(), "payload": {"tracking": "M5-EVAL"}}
        raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
        webhook_headers = {"Content-Type": "application/json", "X-Provider-Event-Id": event["event_id"], "X-Provider-Timestamp": datetime.now(timezone.utc).isoformat(), "X-Provider-Signature": "sha256=" + hmac.new(self.secret.encode(), raw, hashlib.sha256).hexdigest()}
        callback = self.client.post("/internal/fulfillment/webhooks/demo_fulfillment", content=raw, headers=webhook_headers)
        if callback.status_code != 200 or callback.json().get("status") != "applied":
            raise EvaluationFailure(f"signed callback was not applied: {callback.text}")
        return case_id

    def execute(self, row: dict) -> None:
        case_id = self._prepare_case(row["id"])
        with self.sessions() as db:
            before = db.scalar(select(func.count()).select_from(FulfillmentEvent).where(FulfillmentEvent.case_id == case_id))
        response = self.client.post(f"/agent/threads/{self.prefix}-{row['id']}/messages", headers={"X-Demo-User-Id": "U001"}, json={"message": row["message"].format(case_id=case_id), "message_id": f"{row['id']}-message"})
        if response.status_code != 200 or response.json().get("status") != "completed":
            raise EvaluationFailure(f"Agent fulfillment query failed: {response.text}")
        result = response.json()
        with self.sessions() as db:
            after = db.scalar(select(func.count()).select_from(FulfillmentEvent).where(FulfillmentEvent.case_id == case_id))
            case = db.get(AfterSalesCase, case_id)
            calls = list(db.scalars(select(AgentToolCall).where(AgentToolCall.run_id == result["run_id"])))
        if case is None or case.status != "picked_up" or before != after:
            raise EvaluationFailure("Agent changed the trusted fulfillment fact")
        if [(call.tool_name, call.status) for call in calls] != [("get_fulfillment_status", "succeeded")]:
            raise EvaluationFailure("Agent used a write tool or did not read the fulfillment fact")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("EVAL_BASE_URL", "http://127.0.0.1:8005"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--webhook-secret", default=os.getenv("FULFILLMENT_WEBHOOK_SECRET"))
    parser.add_argument("--cases", default="evals/cases/m5_fulfillment_agent_cases.jsonl")
    parser.add_argument("--report", default=None)
    args = parser.parse_args()
    if not args.database_url or not args.webhook_secret:
        raise SystemExit("DATABASE_URL and FULFILLMENT_WEBHOOK_SECRET are required")
    evaluator = M5Evaluator(args.base_url, args.database_url, args.webhook_secret)
    results = []
    try:
        for row in (json.loads(line) for line in Path(args.cases).read_text(encoding="utf-8").splitlines() if line.strip()):
            started = datetime.now(timezone.utc)
            try:
                evaluator.execute(row)
                results.append({"id": row["id"], "status": "passed", "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000)})
            except Exception as error:
                results.append({"id": row["id"], "status": "failed", "error": str(error), "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000)})
    finally:
        evaluator.close()
    report = {"total": len(results), "passed": sum(item["status"] == "passed" for item in results), "failed": sum(item["status"] == "failed" for item in results), "results": results}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
