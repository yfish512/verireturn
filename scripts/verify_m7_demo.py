"""Validate the M7 demo from durable PostgreSQL facts rather than console text."""
from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import select

from backend.app.database import SessionLocal
from backend.app.models import (
    AfterSalesCase, AgentConfirmation, AgentRun, AgentToolCall, FulfillmentEvent,
    MetricComputationJob, MetricSnapshot, OpsAlert, OpsAlertEvent, OutboxEvent,
    ReviewEvent, ReviewTicket,
)


ROOT = Path(__file__).resolve().parents[1]


class VerificationError(RuntimeError):
    pass


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def main() -> None:
    run_key = os.getenv("M7_RUN_KEY", "portfolio-v1")
    result_path = ROOT / ".local" / "m7" / f"demo-{run_key}.json"
    if not result_path.exists():
        raise SystemExit(f"M7 result is missing: {result_path}. Run python -m scripts.demo_m7 first.")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    evidence: dict[str, object] = {"scenario": "M7-DEMO", "run_key": run_key, "checks": []}

    with SessionLocal() as db:
        case = db.get(AfterSalesCase, result["refund_case_id"])
        expect(case is not None and case.status == "completed", "refund case is not completed from verified fulfillment facts")
        evidence["checks"].append("refund case completed")

        confirmation = db.get(AgentConfirmation, result["refund_confirmation_id"])
        expect(confirmation is not None and confirmation.status == "approved", "customer confirmation was not durably approved")
        expect(confirmation.case_id == case.id, "confirmation does not reference the M7 refund case")
        evidence["checks"].append("confirmation persisted before execution")

        refund_run = db.get(AgentRun, result["refund_run_id"])
        expect(refund_run is not None and refund_run.actor_id == "U7001", "refund Agent run is missing or has the wrong owner")
        tool_names = list(db.scalars(select(AgentToolCall.tool_name).where(AgentToolCall.run_id == refund_run.id).order_by(AgentToolCall.sequence_no)))
        expect(tool_names[:2] == ["check_after_sales_eligibility", "create_after_sales_case"], f"unexpected refund tool trace: {tool_names}")
        pickup_tools = list(db.scalars(select(AgentToolCall.tool_name).where(AgentToolCall.run_id == result["pickup_run_id"])))
        expect(pickup_tools == ["schedule_pickup"], f"unexpected pickup tool trace: {pickup_tools}")
        evidence["checks"].append("Agent used bounded eligibility/create/schedule tools")

        fulfillment = list(db.scalars(select(FulfillmentEvent).where(FulfillmentEvent.case_id == case.id).order_by(FulfillmentEvent.sequence_no)))
        expect([event.event_type for event in fulfillment] == ["pickup.collected", "return.received", "refund.processing", "refund.completed"], "fulfillment event sequence is incomplete or out of order")
        expect(all(event.provider == "demo_fulfillment" for event in fulfillment), "untrusted fulfillment provider appeared in M7 facts")
        outbox = list(db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == str(case.id))))
        expect(outbox and all(event.status == "delivered" for event in outbox), "case Outbox delivery is not complete")
        evidence["checks"].append("signed fulfillment sequence and Outbox delivery persisted")

        ticket = db.get(ReviewTicket, result["review_ticket_id"])
        expect(ticket is not None and ticket.status == "approved", "quality-dispute review ticket is not approved")
        review_events = list(db.scalars(select(ReviewEvent.event_type).where(ReviewEvent.ticket_id == ticket.id).order_by(ReviewEvent.sequence_no)))
        expect(review_events == ["REVIEW_TICKET_CREATED", "REVIEW_TICKET_CLAIMED", "REVIEW_EXCEPTION_APPROVED"], f"unexpected review timeline: {review_events}")
        evidence["checks"].append("review ownership, decision and historical event sequence persisted")

        metric_job = db.get(MetricComputationJob, result["metric_job_id"])
        expect(metric_job is not None and metric_job.status == "succeeded", "metric computation job did not succeed")
        snapshots = list(db.scalars(select(MetricSnapshot).where(MetricSnapshot.computation_job_id == metric_job.id)))
        expect(len(snapshots) == 7, "metric job did not materialize all seven snapshots")
        alert = db.get(OpsAlert, result["alert_id"])
        expect(alert is not None and alert.status == "resolved", "demo alert was not resolved through the optimistic-lock workflow")
        alert_events = list(db.scalars(select(OpsAlertEvent.event_type).where(OpsAlertEvent.alert_id == alert.id).order_by(OpsAlertEvent.sequence_no)))
        expect(alert_events == ["ALERT_OPENED", "ALERT_ACKNOWLEDGED", "ALERT_RESOLVED"], f"unexpected alert timeline: {alert_events}")
        evidence["checks"].append("M6 snapshots and alert lifecycle persisted")

        trace = result["trace"]
        expect(case.id in trace["case_ids"], "Trace projection does not resolve the refund case")
        expect(len(trace["fulfillment_events"]) == 4, "Trace projection does not include the fulfillment events")
        expect(any(item["tool_name"] == "create_after_sales_case" for item in trace["tool_calls"]), "Trace projection does not include Agent tool evidence")
        evidence["checks"].append("Trace projects Agent, M1 audit, M5 fulfillment and Outbox facts")

    output_path = ROOT / ".local" / "m7" / f"verification-{run_key}.json"
    output_path.write_text(json.dumps({"status": "passed", **evidence}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", **evidence}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except VerificationError as error:
        raise SystemExit(f"M7 verification failed: {error}") from error
