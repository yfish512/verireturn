"""Execute the M2 natural-language contract against a running Agent service.

Use an isolated PostgreSQL database and a dedicated server process. The runner
checks M1's database facts after each workflow; it does not grade prose.
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
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.models import AfterSalesCase, AgentRun, AgentToolCall, AuditLog


class EvaluationFailure(AssertionError):
    pass


class M2Evaluator:
    def __init__(self, base_url: str, database_url: str):
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=45)
        self.sessions = sessionmaker(bind=create_engine(database_url))
        self.run_prefix = f"m2-eval-{uuid4().hex[:12]}"

    def close(self) -> None:
        self.client.close()

    def send(self, actor: str, thread_id: str, message: str, message_id: str):
        response = self.client.post(
            f"/agent/threads/{thread_id}/messages",
            headers={"X-Demo-User-Id": actor},
            json={"message": message, "message_id": message_id},
        )
        return response

    def confirmation(self, actor: str, confirmation_id: str, approved: bool):
        return self.client.post(
            f"/agent/confirmations/{confirmation_id}",
            headers={"X-Demo-User-Id": actor},
            json={"approved": approved},
        )

    @staticmethod
    def detail_code(response: httpx.Response) -> str:
        try:
            return response.json().get("detail", {}).get("code", "")
        except ValueError:
            return ""

    def case(self, case_id: int) -> AfterSalesCase:
        with self.sessions() as db:
            case = db.get(AfterSalesCase, case_id)
            if case is None:
                raise EvaluationFailure(f"M1 case {case_id} is missing")
            db.expunge(case)
            return case

    def events(self, case_id: int) -> list[AuditLog]:
        with self.sessions() as db:
            return list(db.scalars(select(AuditLog).where(AuditLog.case_id == case_id).order_by(AuditLog.id)))

    def trace_count(self, run_id: str) -> int:
        with self.sessions() as db:
            return len(list(db.scalars(select(AgentToolCall).where(AgentToolCall.run_id == run_id))))

    def pending(self, row: dict, thread: str):
        response = self.send(row["actor_id"], thread, row["message"], f"{row['id']}-message")
        if response.status_code != 200:
            raise EvaluationFailure(f"expected Agent 200, got {response.status_code}: {response.text}")
        payload = response.json()
        if payload["status"] != "awaiting_confirmation" or not payload.get("confirmation_id") or not payload.get("case_id"):
            raise EvaluationFailure(f"expected pending confirmation, got {payload}")
        return payload

    def execute(self, row: dict) -> None:
        kind = row["kind"]
        thread = f"{self.run_prefix}-{row['id']}"
        actor = row["actor_id"]
        if kind in {"create_approve", "create_reject", "schedule", "missing_slot", "duplicate_pending", "cross_user_confirmation", "confirmation_once"}:
            pending = self.pending(row, thread)
            case_id = pending["case_id"]
            if kind == "create_approve":
                result = self.confirmation(actor, pending["confirmation_id"], True)
                if result.status_code != 200 or self.case(case_id).status != "awaiting_pickup":
                    raise EvaluationFailure("approval did not produce awaiting_pickup")
            elif kind == "create_reject":
                result = self.confirmation(actor, pending["confirmation_id"], False)
                if result.status_code != 200 or self.case(case_id).status != "cancelled":
                    raise EvaluationFailure("rejection did not cancel case")
            elif kind == "schedule":
                if self.confirmation(actor, pending["confirmation_id"], True).status_code != 200:
                    raise EvaluationFailure("approval failed before pickup")
                scheduled = self.send(actor, thread, "明天上午取件", f"{row['id']}-pickup")
                if scheduled.status_code != 200 or self.case(case_id).status != "pickup_scheduled":
                    raise EvaluationFailure("pickup scheduling did not reach pickup_scheduled")
            elif kind == "missing_slot":
                self.confirmation(actor, pending["confirmation_id"], True)
                slot = self.send(actor, thread, "取件", f"{row['id']}-no-slot")
                if slot.status_code != 200 or "取件时段" not in slot.json().get("response", ""):
                    raise EvaluationFailure("missing pickup slot did not trigger clarification")
            elif kind == "duplicate_pending":
                duplicate = self.send(actor, thread, row["message"], f"{row['id']}-message")
                if duplicate.status_code != 200 or duplicate.json().get("confirmation_id") != pending["confirmation_id"]:
                    raise EvaluationFailure("duplicate pending message did not return original confirmation")
                with self.sessions() as db:
                    runs = list(db.scalars(select(AgentRun).where(AgentRun.thread_id == thread)))
                if len(runs) != 1:
                    raise EvaluationFailure("duplicate pending message created another run")
            elif kind == "cross_user_confirmation":
                other = self.confirmation("U002", pending["confirmation_id"], True)
                if other.status_code != 404 or self.case(case_id).status != "pending_confirmation":
                    raise EvaluationFailure("cross-user confirmation was not rejected")
            else:  # confirmation_once
                first = self.confirmation(actor, pending["confirmation_id"], True)
                second = self.confirmation(actor, pending["confirmation_id"], True)
                events = self.events(case_id)
                if first.status_code != 200 or second.status_code != 409 or [event.event_type for event in events].count("CASE_CONFIRMED") != 1:
                    raise EvaluationFailure("confirmation replay was not exactly once")
            return

        response = self.send(actor, thread, row["message"], f"{row['id']}-message")
        if response.status_code != 200:
            raise EvaluationFailure(f"expected Agent 200, got {response.status_code}: {response.text}")
        payload = response.json()
        answer = payload.get("response", "")
        if kind == "query_order":
            if "订单" not in answer or self.trace_count(payload["run_id"]) != 1:
                raise EvaluationFailure("order query did not create one trace")
        elif kind == "query_logistics":
            if "物流状态" not in answer or self.trace_count(payload["run_id"]) != 1:
                raise EvaluationFailure("logistics query did not create one trace")
        elif kind == "eligibility_ok":
            if "可以申请" not in answer:
                raise EvaluationFailure("eligible case was not explained as eligible")
        elif kind == "eligibility_denied":
            if "REFUND_NOT_ELIGIBLE" not in answer:
                raise EvaluationFailure("ineligible refund did not retain policy code")
        elif kind == "cross_user_order":
            if "ORDER_ACCESS_DENIED" not in answer:
                raise EvaluationFailure("cross-user order was not denied")
        elif kind == "missing_order":
            if "订单号" not in answer:
                raise EvaluationFailure("missing order did not trigger clarification")
        elif kind == "unknown":
            if self.trace_count(payload["run_id"]) != 0:
                raise EvaluationFailure("unknown request should not call business tools")
        elif kind == "order_not_found":
            if "ORDER_NOT_FOUND" not in answer:
                raise EvaluationFailure("missing order was not reported")
        elif kind == "case_not_found":
            if "CASE_NOT_FOUND" not in answer:
                raise EvaluationFailure("missing case was not reported")
        elif kind == "trace_visibility":
            trace = self.client.get(f"/agent/runs/{payload['run_id']}/tool-calls", headers={"X-Demo-User-Id": actor})
            if trace.status_code != 200 or not trace.json() or not {"graph_node", "tool_name", "m1_request_id", "latency_ms", "status"}.issubset(trace.json()[0]):
                raise EvaluationFailure("trace endpoint does not expose required fields")
        else:
            raise EvaluationFailure(f"unsupported kind: {kind}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("EVAL_BASE_URL", "http://127.0.0.1:8002"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--cases", default="evals/cases/m2_agent_cases.jsonl")
    parser.add_argument("--report", default=None)
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    cases = [json.loads(line) for line in Path(args.cases).read_text(encoding="utf-8").splitlines() if line.strip()]
    evaluator = M2Evaluator(args.base_url, args.database_url)
    results = []
    try:
        for row in cases:
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
