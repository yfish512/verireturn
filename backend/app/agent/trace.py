from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AgentConfirmation, AgentReviewConfirmation, AgentRun, AgentToolCall


class TraceStore:
    """Persists M2 observability data; it never writes M1 business tables."""

    def __init__(self, session_factory: Callable[[], Session], confirmation_ttl_seconds: int = 900):
        self.session_factory = session_factory
        self.confirmation_ttl_seconds = confirmation_ttl_seconds

    def start_run(self, thread_id: str, actor_id: str, model_name: str, input_text: str) -> str:
        run_id = str(uuid4())
        with self.session_factory() as db:
            db.add(AgentRun(id=run_id, thread_id=thread_id, actor_id=actor_id, status="running", model_name=model_name, input_text=input_text))
            db.commit()
        return run_id

    def finish_run(self, run_id: str, status: str, response: str | None = None, error_code: str | None = None) -> None:
        with self.session_factory() as db:
            run = db.get(AgentRun, run_id)
            if run is None:
                return
            run.status = status
            run.final_response = response
            run.error_code = error_code
            run.finished_at = datetime.now(timezone.utc) if status in {"completed", "failed", "cancelled"} else None
            db.commit()

    def record_tool_call(
        self, run_id: str, sequence_no: int, graph_node: str, tool_name: str, arguments: dict,
        result: dict | None, request_id: str, latency_ms: int, status: str, error_code: str | None = None,
    ) -> None:
        with self.session_factory() as db:
            db.add(AgentToolCall(
                run_id=run_id, sequence_no=sequence_no, graph_node=graph_node, tool_name=tool_name,
                arguments_json=arguments, result_json=result, m1_request_id=request_id, latency_ms=latency_ms,
                status=status, error_code=error_code,
            ))
            db.commit()

    def create_confirmation(self, thread_id: str, run_id: str, actor_id: str, case_id: int) -> AgentConfirmation:
        confirmation_id = str(uuid4())
        with self.session_factory() as db:
            confirmation = AgentConfirmation(
                id=confirmation_id, thread_id=thread_id, run_id=run_id, actor_id=actor_id, case_id=case_id,
                status="pending", expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.confirmation_ttl_seconds),
            )
            db.add(confirmation)
            db.commit()
            db.refresh(confirmation)
            db.expunge(confirmation)
            return confirmation

    def get_confirmation(self, confirmation_id: str, actor_id: str) -> AgentConfirmation | None:
        with self.session_factory() as db:
            confirmation = db.scalar(select(AgentConfirmation).where(AgentConfirmation.id == confirmation_id, AgentConfirmation.actor_id == actor_id))
            if confirmation is None:
                return None
            expires_at = confirmation.expires_at
            if expires_at.tzinfo is None:  # SQLite test dialect does not round-trip tzinfo.
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if confirmation.status == "pending" and expires_at <= datetime.now(timezone.utc):
                confirmation.status = "expired"
                db.commit()
            db.expunge(confirmation)
            return confirmation

    def get_pending_confirmation(self, thread_id: str, actor_id: str) -> AgentConfirmation | None:
        with self.session_factory() as db:
            confirmation = db.scalar(
                select(AgentConfirmation)
                .where(AgentConfirmation.thread_id == thread_id, AgentConfirmation.actor_id == actor_id, AgentConfirmation.status == "pending")
                .order_by(AgentConfirmation.created_at.desc())
            )
            if confirmation is None:
                return None
            expires_at = confirmation.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                confirmation.status = "expired"
                db.commit()
                return None
            db.expunge(confirmation)
            return confirmation

    def create_review_confirmation(
        self, thread_id: str, run_id: str, actor_id: str, order_id: str, request_type: str, reason: str
    ) -> AgentReviewConfirmation:
        confirmation_id = str(uuid4())
        with self.session_factory() as db:
            confirmation = AgentReviewConfirmation(
                id=confirmation_id, thread_id=thread_id, run_id=run_id, actor_id=actor_id, order_id=order_id,
                request_type=request_type, reason=reason, status="pending",
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.confirmation_ttl_seconds),
            )
            db.add(confirmation)
            db.commit()
            db.refresh(confirmation)
            db.expunge(confirmation)
            return confirmation

    def get_pending_review_confirmation(self, thread_id: str, actor_id: str) -> AgentReviewConfirmation | None:
        with self.session_factory() as db:
            confirmation = db.scalar(
                select(AgentReviewConfirmation)
                .where(AgentReviewConfirmation.thread_id == thread_id, AgentReviewConfirmation.actor_id == actor_id, AgentReviewConfirmation.status == "pending")
                .order_by(AgentReviewConfirmation.created_at.desc())
            )
            if confirmation is None:
                return None
            expires_at = confirmation.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                confirmation.status = "expired"
                db.commit()
                return None
            db.expunge(confirmation)
            return confirmation

    def get_review_confirmation(self, confirmation_id: str, actor_id: str) -> AgentReviewConfirmation | None:
        with self.session_factory() as db:
            confirmation = db.scalar(select(AgentReviewConfirmation).where(
                AgentReviewConfirmation.id == confirmation_id, AgentReviewConfirmation.actor_id == actor_id
            ))
            if confirmation is None:
                return None
            expires_at = confirmation.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if confirmation.status == "pending" and expires_at <= datetime.now(timezone.utc):
                confirmation.status = "expired"
                db.commit()
            db.expunge(confirmation)
            return confirmation

    def resolve_confirmation(self, confirmation_id: str, approved: bool) -> None:
        with self.session_factory() as db:
            confirmation = db.get(AgentConfirmation, confirmation_id)
            if confirmation is None:
                return
            confirmation.status = "approved" if approved else "rejected"
            confirmation.resolved_at = datetime.now(timezone.utc)
            db.commit()

    def resolve_review_confirmation(self, confirmation_id: str, approved: bool, ticket_id: str | None = None) -> None:
        with self.session_factory() as db:
            confirmation = db.get(AgentReviewConfirmation, confirmation_id)
            if confirmation is None:
                return
            confirmation.status = "approved" if approved else "rejected"
            confirmation.ticket_id = ticket_id
            confirmation.resolved_at = datetime.now(timezone.utc)
            db.commit()

    def list_tool_calls(self, run_id: str, actor_id: str) -> list[AgentToolCall]:
        with self.session_factory() as db:
            run = db.get(AgentRun, run_id)
            if run is None or run.actor_id != actor_id:
                return []
            return list(db.scalars(select(AgentToolCall).where(AgentToolCall.run_id == run_id).order_by(AgentToolCall.sequence_no)))
