from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AfterSalesCase, ReviewEvent, ReviewTicket


TERMINAL_REVIEW_STATUSES = {"approved", "rejected", "closed", "expired"}


def _percent(numerator: int, denominator: int) -> float:
    return round(numerator * 100 / denominator, 2) if denominator else 0.0


def _as_utc(value: datetime) -> datetime:
    # SQLite test dialect does not round-trip timezone information; production
    # PostgreSQL values remain timestamptz. Treat a naive test value as UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def review_metrics(db: Session, start_at: datetime | None = None, end_at: datetime | None = None) -> dict:
    """Compute M3 metrics from authoritative transactional tables.

    M3 uses direct PostgreSQL aggregation while data volume is small. The API
    makes the formula explicit so a later materialized view is a performance
    optimization rather than a second source of truth.
    """
    now = datetime.now(timezone.utc)
    ticket_stmt = select(ReviewTicket)
    case_stmt = select(AfterSalesCase)
    if start_at:
        ticket_stmt = ticket_stmt.where(ReviewTicket.created_at >= start_at)
        case_stmt = case_stmt.where(AfterSalesCase.created_at >= start_at)
    if end_at:
        ticket_stmt = ticket_stmt.where(ReviewTicket.created_at < end_at)
        case_stmt = case_stmt.where(AfterSalesCase.created_at < end_at)
    tickets = list(db.scalars(ticket_stmt))
    cases = list(db.scalars(case_stmt))
    by_status = {status: sum(ticket.status == status for ticket in tickets) for status in (
        "open", "claimed", "waiting_customer", "approved", "rejected", "closed", "expired"
    )}
    terminal = [ticket for ticket in tickets if ticket.status in TERMINAL_REVIEW_STATUSES]
    resolution_minutes: list[float] = []
    for ticket in terminal:
        event = db.scalar(
            select(ReviewEvent).where(
                ReviewEvent.ticket_id == ticket.id,
                ReviewEvent.event_type.in_(("REVIEW_EXCEPTION_APPROVED", "REVIEW_REJECTED", "REVIEW_DUPLICATE_CLOSED")),
            ).order_by(ReviewEvent.created_at.desc()).limit(1)
        )
        if event:
            resolution_minutes.append(max(0.0, (_as_utc(event.created_at) - _as_utc(ticket.created_at)).total_seconds() / 60))
    resolution_minutes.sort()
    p95 = resolution_minutes[min(len(resolution_minutes) - 1, int((len(resolution_minutes) - 1) * 0.95))] if resolution_minutes else None
    direct_cases = sum(case.source_review_ticket_id is None for case in cases)
    total_routed = direct_cases + len(tickets)
    approved = by_status["approved"]
    rejected = by_status["rejected"]
    return {
        "tickets_total": len(tickets),
        "tickets_by_status": by_status,
        "backlog": by_status["open"] + by_status["claimed"] + by_status["waiting_customer"],
        "sla_breached": sum(ticket.status not in TERMINAL_REVIEW_STATUSES and _as_utc(ticket.due_at) < now for ticket in tickets),
        "review_approval_rate": _percent(approved, approved + rejected),
        "manual_intervention_rate": _percent(len(tickets), total_routed),
        "automatic_case_share": _percent(direct_cases, total_routed),
        "p95_review_resolution_minutes": round(p95, 2) if p95 is not None else None,
    }
