"""Run real-model M3 review workflows against a live Agent service.

The runner verifies database facts, not response prose: confirmation before
ticket creation, ownership, duplicate protection, operator approval, the new
M1 case, and the customer's second confirmation.
"""

from __future__ import annotations

import argparse
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

from backend.app.models import AfterSalesCase, AgentReviewConfirmation, AgentToolCall, AuditLog, ReviewEvent, ReviewTicket


class EvaluationFailure(AssertionError):
    pass


class M3Evaluator:
    def __init__(self, base_url: str, database_url: str):
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=45)
        self.sessions = sessionmaker(bind=create_engine(database_url))
        self.prefix = f"m3-eval-{uuid4().hex[:12]}"

    def close(self) -> None:
        self.client.close()

    def _send(self, thread: str, message: str, message_id: str, actor: str = "U001") -> dict:
        response = self.client.post(f"/agent/threads/{thread}/messages", headers={"X-Demo-User-Id": actor}, json={"message": message, "message_id": message_id})
        if response.status_code != 200:
            raise EvaluationFailure(f"Agent response {response.status_code}: {response.text}")
        return response.json()

    def _confirm(self, confirmation_id: str, approved: bool, actor: str = "U001") -> httpx.Response:
        return self.client.post(f"/agent/confirmations/{confirmation_id}", headers={"X-Demo-User-Id": actor}, json={"approved": approved})

    def _pending(self, thread: str, row: dict) -> tuple[dict, int]:
        with self.sessions() as db:
            before = db.scalar(select(func.count()).select_from(ReviewTicket))
        result = self._send(thread, row["message"], f"{row['id']}-message")
        if result.get("status") != "awaiting_confirmation" or not result.get("confirmation_id") or result.get("case_id") is not None:
            raise EvaluationFailure(f"expected pending review confirmation, got {result}")
        with self.sessions() as db:
            confirmation = db.get(AgentReviewConfirmation, result["confirmation_id"])
            trace_count = db.scalar(select(func.count()).select_from(AgentToolCall).where(AgentToolCall.run_id == result["run_id"]))
            after = db.scalar(select(func.count()).select_from(ReviewTicket))
        if confirmation is None or confirmation.status != "pending" or confirmation.ticket_id is not None or trace_count != 0 or before != after:
            raise EvaluationFailure("review ticket or tool trace was created before customer confirmation")
        return result, before

    def _approved_ticket(self, pending: dict, before: int) -> ReviewTicket:
        response = self._confirm(pending["confirmation_id"], True)
        if response.status_code != 200:
            raise EvaluationFailure(f"review confirmation failed: {response.text}")
        ticket_id = response.json().get("ticket_id")
        if not ticket_id:
            raise EvaluationFailure("approved review confirmation did not return a ticket")
        with self.sessions() as db:
            ticket = db.get(ReviewTicket, ticket_id)
            confirmation = db.get(AgentReviewConfirmation, pending["confirmation_id"])
            trace_count = db.scalar(select(func.count()).select_from(AgentToolCall).where(AgentToolCall.run_id == pending["run_id"]))
            after = db.scalar(select(func.count()).select_from(ReviewTicket))
            if ticket is None:
                raise EvaluationFailure("approved review ticket missing in database")
            db.expunge(ticket)
        if ticket.status != "open" or confirmation.status != "approved" or confirmation.ticket_id != ticket.id or trace_count != 1 or after != before + 1:
            raise EvaluationFailure("approved review confirmation has inconsistent persisted state")
        return ticket

    def execute(self, row: dict) -> None:
        thread = f"{self.prefix}-{row['id']}"
        pending, before = self._pending(thread, row)
        kind = row["kind"]
        if kind == "reject":
            response = self._confirm(pending["confirmation_id"], False)
            if response.status_code != 200:
                raise EvaluationFailure("review rejection failed")
            with self.sessions() as db:
                confirmation = db.get(AgentReviewConfirmation, pending["confirmation_id"])
                after = db.scalar(select(func.count()).select_from(ReviewTicket))
            if confirmation.status != "rejected" or confirmation.ticket_id is not None or after != before:
                raise EvaluationFailure("rejected review confirmation created a ticket")
            return
        if kind == "duplicate":
            duplicate = self._send(thread, row["message"], f"{row['id']}-message")
            if duplicate.get("confirmation_id") != pending["confirmation_id"]:
                raise EvaluationFailure("duplicate review message did not return the original confirmation")
        if kind == "cross_user":
            other = self._confirm(pending["confirmation_id"], True, actor="U002")
            if other.status_code != 404:
                raise EvaluationFailure("other user resolved a review confirmation")
        ticket = self._approved_ticket(pending, before)
        if kind != "approve":
            return
        claim = self.client.post(
            f"/ops/review-tickets/{ticket.id}/claim",
            headers={"X-Demo-User-Id": "OPS001", "Idempotency-Key": f"{self.prefix}-{row['id']}-claim-key"},
            json={"expected_version": ticket.version},
        )
        if claim.status_code != 200:
            raise EvaluationFailure(f"operator claim failed: {claim.text}")
        decision = self.client.post(
            f"/ops/review-tickets/{ticket.id}/decisions",
            headers={"X-Demo-User-Id": "OPS001", "Idempotency-Key": f"{self.prefix}-{row['id']}-approve-key"},
            json={"action": "approve_exception", "expected_version": claim.json()["version"], "reason_code": "EVAL_APPROVED", "customer_message": "评测审核批准。"},
        )
        if decision.status_code != 200 or decision.json()["status"] != "approved":
            raise EvaluationFailure(f"operator approval failed: {decision.text}")
        with self.sessions() as db:
            case = db.scalar(select(AfterSalesCase).where(AfterSalesCase.source_review_ticket_id == ticket.id))
            events = list(db.scalars(select(ReviewEvent).where(ReviewEvent.ticket_id == ticket.id).order_by(ReviewEvent.sequence_no)))
        if case is None or case.status != "pending_confirmation" or [event.event_type for event in events] != ["REVIEW_TICKET_CREATED", "REVIEW_TICKET_CLAIMED", "REVIEW_EXCEPTION_APPROVED"]:
            raise EvaluationFailure("approval did not produce the expected case and review events")
        customer = self.client.post(
            f"/tools/after-sales/cases/{case.id}/confirm",
            headers={"X-Demo-User-Id": "U001", "Idempotency-Key": f"{self.prefix}-{row['id']}-customer-confirm"},
        )
        if customer.status_code != 200 or customer.json()["status"] != "awaiting_pickup":
            raise EvaluationFailure("customer second confirmation failed")
        with self.sessions() as db:
            audit = list(db.scalars(select(AuditLog).where(AuditLog.case_id == case.id).order_by(AuditLog.id)))
        if [event.event_type for event in audit] != ["CASE_CREATED_FROM_REVIEW", "CASE_CONFIRMED"]:
            raise EvaluationFailure("replacement case audit is incomplete")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("EVAL_BASE_URL", "http://127.0.0.1:8003"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--cases", default="evals/cases/m3_agent_cases.jsonl")
    parser.add_argument("--report", default=None)
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    evaluator = M3Evaluator(args.base_url, args.database_url)
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
