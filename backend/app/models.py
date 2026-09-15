from datetime import datetime, timezone
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))


class Actor(Base):
    """Server-side role mapping; callers never provide a role in request bodies."""

    __tablename__ = "actors"
    __table_args__ = (CheckConstraint("role IN ('customer', 'operator', 'ops_manager', 'internal_service')", name="ck_actor_role"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    item_name: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(32), default="delivered")
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    condition: Mapped[str] = mapped_column(String(32), default="sealed")
    quality_issue: Mapped[bool] = mapped_column(Boolean, default=False)


class Logistics(Base):
    __tablename__ = "logistics"

    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    tracking_number: Mapped[str] = mapped_column(String(64))


class AfterSalesCase(Base):
    __tablename__ = "after_sales_cases"
    __table_args__ = (
        CheckConstraint("request_type IN ('refund', 'exchange')", name="ck_after_sales_request_type"),
        CheckConstraint(
            "status IN ('pending_confirmation', 'awaiting_pickup', 'pickup_scheduled', 'picked_up', 'return_received', 'refund_processing', 'replacement_shipped', 'fulfillment_exception', 'completed', 'cancelled', 'manual_review')",
            name="ck_after_sales_status",
        ),
        Index("ix_after_sales_cases_user_status_created_at", "user_id", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    request_type: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending_confirmation")
    eligible_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    operation: Mapped[str] = mapped_column(String(64), default="create_after_sales_case")
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    pickup_slot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    policy_version_id: Mapped[str | None] = mapped_column(ForeignKey("policy_versions.id"), nullable=True)
    source_review_ticket_id: Mapped[str | None] = mapped_column(ForeignKey("review_tickets.id"), unique=True, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("after_sales_cases.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text)
    actor_type: Mapped[str] = mapped_column(String(32), default="system")
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class IdempotencyRecord(Base):
    """Stores command results so retries cannot repeat state-changing work."""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("actor_id", "operation", "idempotency_key", name="uq_idempotency_actor_operation_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(32), default="after_sales_case")
    resource_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    model_name: Mapped[str] = mapped_column(String(128))
    input_text: Mapped[str] = mapped_column(Text)
    final_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"
    __table_args__ = (UniqueConstraint("run_id", "sequence_no", name="uq_agent_tool_call_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    sequence_no: Mapped[int] = mapped_column(Integer)
    graph_node: Mapped[str] = mapped_column(String(64))
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments_json: Mapped[dict] = mapped_column(JSON)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    m1_request_id: Mapped[str] = mapped_column(String(64))
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AgentConfirmation(Base):
    __tablename__ = "agent_confirmations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("after_sales_cases.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published', 'retired')", name="ck_policy_version_status"),
        UniqueConstraint("version", name="uq_policy_versions_version"),
        Index(
            "uq_policy_versions_single_published", "status", unique=True,
            postgresql_where=text("status = 'published'"), sqlite_where=text("status = 'published'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="draft")
    rules_json: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    published_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ReviewTicket(Base):
    __tablename__ = "review_tickets"
    __table_args__ = (
        CheckConstraint("request_type IN ('refund', 'exchange')", name="ck_review_ticket_request_type"),
        CheckConstraint("priority IN ('normal', 'high')", name="ck_review_ticket_priority"),
        CheckConstraint(
            "status IN ('open', 'claimed', 'waiting_customer', 'approved', 'rejected', 'closed', 'expired')",
            name="ck_review_ticket_status",
        ),
        Index("ix_review_tickets_queue", "status", "priority", "due_at", "created_at"),
        Index("ix_review_tickets_assignee", "assigned_operator_id", "status", "updated_at"),
        Index("ix_review_tickets_requester_created", "requester_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    # Kept as a historical reference rather than an FK so the replacement-case
    # relation can stay a strict unique FK without creating a schema cycle.
    source_case_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True, index=True)
    request_type: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    request_snapshot: Mapped[dict] = mapped_column(JSON)
    trigger_code: Mapped[str] = mapped_column(String(64))
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    assigned_operator_id: Mapped[str | None] = mapped_column(ForeignKey("actors.id"), nullable=True, index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("policy_versions.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class ReviewEvent(Base):
    __tablename__ = "review_events"
    __table_args__ = (UniqueConstraint("ticket_id", "sequence_no", name="uq_review_event_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("review_tickets.id"), index=True)
    sequence_no: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    actor_id: Mapped[str] = mapped_column(String(64))
    actor_role: Mapped[str] = mapped_column(String(32))
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AgentReviewConfirmation(Base):
    """Persisted customer approval required before an Agent submits a review ticket."""

    __tablename__ = "agent_review_confirmations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    request_type: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    ticket_id: Mapped[str | None] = mapped_column(ForeignKey("review_tickets.id"), nullable=True, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# M5 keeps the outbound intent and inbound external fact separate.  A provider
# acknowledgement is never itself treated as a fulfillment state transition;
# only a verified Inbox event can produce an immutable FulfillmentEvent.
class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'processing', 'delivered', 'dead')", name="ck_outbox_event_status"),
        UniqueConstraint("aggregate_type", "aggregate_id", "event_type", "idempotency_key", name="uq_outbox_business_intent"),
        Index("ix_outbox_events_claim", "status", "next_attempt_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(32))
    aggregate_id: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(64))
    destination: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutboxDelivery(Base):
    __tablename__ = "outbox_deliveries"
    __table_args__ = (
        CheckConstraint("status IN ('succeeded', 'failed')", name="ck_outbox_delivery_status"),
        UniqueConstraint("outbox_event_id", "attempt_no", name="uq_outbox_delivery_attempt"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    outbox_event_id: Mapped[str] = mapped_column(ForeignKey("outbox_events.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    provider_idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    response_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class InboxEvent(Base):
    __tablename__ = "inbox_events"
    __table_args__ = (
        CheckConstraint("status IN ('received', 'applied', 'deferred', 'rejected')", name="ck_inbox_event_status"),
        UniqueConstraint("provider", "provider_event_id", name="uq_inbox_provider_event"),
        Index("ix_inbox_events_deferred", "status", "received_at"),
        Index("ix_inbox_events_case_sequence", "case_id", "sequence_no"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_event_id: Mapped[str] = mapped_column(String(128))
    case_id: Mapped[int] = mapped_column(ForeignKey("after_sales_cases.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    sequence_no: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="received")
    rejection_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FulfillmentEvent(Base):
    __tablename__ = "fulfillment_events"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_fulfillment_provider_event"),
        UniqueConstraint("case_id", "sequence_no", name="uq_fulfillment_case_sequence"),
        Index("ix_fulfillment_events_case_created", "case_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("after_sales_cases.id"), index=True)
    inbox_event_id: Mapped[str] = mapped_column(ForeignKey("inbox_events.id"), unique=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_event_id: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(64))
    sequence_no: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class CustomerNotification(Base):
    __tablename__ = "customer_notifications"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_customer_notification_status"),
        UniqueConstraint("case_id", "dedupe_key", name="uq_customer_notification_dedupe"),
        Index("ix_customer_notifications_case_created", "case_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("after_sales_cases.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    channel: Mapped[str] = mapped_column(String(32), default="in_app")
    template: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class FulfillmentIncident(Base):
    __tablename__ = "fulfillment_incidents"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'acknowledged', 'resolved')", name="ck_fulfillment_incident_status"),
        UniqueConstraint("case_id", "dedupe_key", name="uq_fulfillment_incident_dedupe"),
        Index("ix_fulfillment_incidents_queue", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("after_sales_cases.id"), index=True)
    inbox_event_id: Mapped[str | None] = mapped_column(ForeignKey("inbox_events.id"), nullable=True)
    incident_type: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="open")
    assigned_operator_id: Mapped[str | None] = mapped_column(ForeignKey("actors.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


# M6 is an operational read/measure layer.  These rows never participate in
# M1-M5 state transitions; their own events are nevertheless versioned and
# auditable so operational conclusions can be reproduced.
class AgentRunFeedback(Base):
    __tablename__ = "agent_run_feedback"
    __table_args__ = (
        CheckConstraint("rating IN ('helpful', 'unhelpful')", name="ck_agent_run_feedback_rating"),
        UniqueConstraint("run_id", "actor_id", name="uq_agent_run_feedback_actor"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    rating: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MetricComputationJob(Base):
    __tablename__ = "metric_computation_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_metric_computation_job_status"),
        UniqueConstraint("window_start", "window_end", name="uq_metric_computation_window"),
        Index("ix_metric_computation_job_claim", "status", "lease_until", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (
        UniqueConstraint("metric_name", "window_start", "window_end", "dimension_key", name="uq_metric_snapshot_window"),
        Index("ix_metric_snapshots_name_window", "metric_name", "window_end"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    metric_name: Mapped[str] = mapped_column(String(96))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dimensions: Mapped[dict] = mapped_column(JSON, default=dict)
    dimension_key: Mapped[str] = mapped_column(String(64), default="all")
    value: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    computation_job_id: Mapped[str] = mapped_column(ForeignKey("metric_computation_jobs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AlertRuleVersion(Base):
    __tablename__ = "alert_rule_versions"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published', 'retired')", name="ck_alert_rule_version_status"),
        UniqueConstraint("rule_key", "version", name="uq_alert_rule_version"),
        Index("uq_alert_rule_single_published", "rule_key", unique=True, postgresql_where=text("status = 'published'"), sqlite_where=text("status = 'published'")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_key: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    metric_name: Mapped[str] = mapped_column(String(96))
    comparison: Mapped[str] = mapped_column(String(4))
    threshold: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    created_by: Mapped[str] = mapped_column(ForeignKey("actors.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OpsAlert(Base):
    __tablename__ = "ops_alerts"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'acknowledged', 'resolved', 'muted')", name="ck_ops_alert_status"),
        UniqueConstraint("rule_version_id", "fingerprint", name="uq_ops_alert_fingerprint"),
        Index("ix_ops_alerts_queue", "status", "severity", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_version_id: Mapped[str] = mapped_column(ForeignKey("alert_rule_versions.id"), index=True)
    metric_snapshot_id: Mapped[str] = mapped_column(ForeignKey("metric_snapshots.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(128))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="open")
    assigned_operator_id: Mapped[str | None] = mapped_column(ForeignKey("actors.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class OpsAlertEvent(Base):
    __tablename__ = "ops_alert_events"
    __table_args__ = (UniqueConstraint("alert_id", "sequence_no", name="uq_ops_alert_event_sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("ops_alerts.id"), index=True)
    sequence_no: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    actor_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class EvaluationSuite(Base):
    __tablename__ = "evaluation_suites"
    __table_args__ = (UniqueConstraint("suite_key", "version", name="uq_evaluation_suite_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    suite_key: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    case_path: Mapped[str] = mapped_column(String(256))
    checksum: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(ForeignKey("actors.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_evaluation_run_status"),
        Index("ix_evaluation_run_claim", "status", "lease_until", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("evaluation_suites.id"), index=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("actors.id"))
    model_name: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64), default="agent-prompt-v1")
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    report_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (UniqueConstraint("evaluation_run_id", "case_id", name="uq_evaluation_result_case"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evaluation_run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"), index=True)
    case_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    latency_ms: Mapped[int] = mapped_column(Integer)
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# M4 knowledge is deliberately versioned separately from transactional policy.
# A document can explain a policy, but it can never become an input that changes
# an order's eligibility, amount, permission or workflow state.
class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        CheckConstraint("audience IN ('customer', 'operator', 'shared')", name="ck_knowledge_document_audience"),
        UniqueConstraint("stable_key", name="uq_knowledge_document_stable_key"),
        Index("ix_knowledge_documents_audience_category", "audience", "category"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stable_key: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(200))
    audience: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(ForeignKey("actors.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class KnowledgeDocumentVersion(Base):
    __tablename__ = "knowledge_document_versions"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'indexing', 'ready', 'published', 'retired')", name="ck_knowledge_document_version_status"),
        UniqueConstraint("document_id", "version", name="uq_knowledge_document_version"),
        Index(
            "uq_knowledge_one_published_version", "document_id", unique=True,
            postgresql_where=text("status = 'published'"), sqlite_where=text("status = 'published'"),
        ),
        Index("ix_knowledge_versions_status", "status", "published_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    content_markdown: Mapped[str] = mapped_column(Text)
    content_checksum: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str] = mapped_column(String(128))
    published_by: Mapped[str | None] = mapped_column(ForeignKey("actors.id"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_version_id", "ordinal", name="uq_knowledge_chunk_ordinal"),
        Index("ix_knowledge_chunks_version", "document_version_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_version_id: Mapped[str] = mapped_column(ForeignKey("knowledge_document_versions.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    # jieba tokens joined by spaces; the PostgreSQL GIN expression index is
    # built over this value using the simple dictionary.
    search_text: Mapped[str] = mapped_column(Text)
    chunk_checksum: Mapped[str] = mapped_column(String(64))
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(512).with_variant(JSON(), "sqlite"), nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class KnowledgeIngestionJob(Base):
    __tablename__ = "knowledge_ingestion_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_knowledge_ingestion_job_status"),
        UniqueConstraint("document_version_id", name="uq_knowledge_ingestion_job_version"),
        Index("ix_knowledge_ingestion_jobs_claim", "status", "lease_until", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_version_id: Mapped[str] = mapped_column(ForeignKey("knowledge_document_versions.id"))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class KnowledgeRetrievalLog(Base):
    __tablename__ = "knowledge_retrieval_logs"
    __table_args__ = (Index("ix_knowledge_retrieval_logs_actor_created", "actor_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True, index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("actors.id"), index=True)
    route: Mapped[str] = mapped_column(String(32))
    query_digest: Mapped[str] = mapped_column(String(64))
    # Persist the full server-computed scope, e.g. customer|operator|shared,
    # rather than a client-provided role value.
    audience: Mapped[str] = mapped_column(String(64))
    candidate_chunk_ids: Mapped[list[str]] = mapped_column(JSON)
    cited_chunk_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class KnowledgeFeedback(Base):
    __tablename__ = "knowledge_feedback"
    __table_args__ = (UniqueConstraint("retrieval_log_id", "actor_id", name="uq_knowledge_feedback_actor_retrieval"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    retrieval_log_id: Mapped[str] = mapped_column(ForeignKey("knowledge_retrieval_logs.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("actors.id"))
    rating: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AgentThread(Base):
    """Customer-owned conversation container; IDs are never shared across actors."""

    __tablename__ = "agent_threads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    memory_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AgentMessageRecord(Base):
    """Append-only conversation transcript with a client retry key on user turns."""

    __tablename__ = "agent_message_records"
    __table_args__ = (
        CheckConstraint("role IN ('customer', 'agent')", name="ck_agent_message_record_role"),
        UniqueConstraint("thread_id", "client_message_id", name="uq_agent_message_client_id"),
        UniqueConstraint("thread_id", "sequence_no", name="uq_agent_message_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("agent_threads.id"), index=True)
    sequence_no: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    client_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reply_to_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True, index=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AgentTask(Base):
    """The one explicit, customer-visible task currently active in a thread."""

    __tablename__ = "agent_tasks"
    __table_args__ = (
        CheckConstraint(
            "phase IN ('collecting_slots', 'ready_to_execute', 'awaiting_customer_confirmation', 'awaiting_pickup_slot', 'completed', 'cancelled', 'expired')",
            name="ck_agent_task_phase",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("agent_threads.id"), unique=True)
    intent: Mapped[str] = mapped_column(String(64))
    phase: Mapped[str] = mapped_column(String(48), default="collecting_slots")
    slots_json: Mapped[dict] = mapped_column(JSON, default=dict)
    missing_slots: Mapped[list] = mapped_column(JSON, default=list)
    active_case_id: Mapped[int | None] = mapped_column(ForeignKey("after_sales_cases.id"), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class AgentTaskEvent(Base):
    __tablename__ = "agent_task_events"
    __table_args__ = (UniqueConstraint("task_id", "sequence_no", name="uq_agent_task_event_sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True)
    sequence_no: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSON)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("agent_message_records.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

# M9 payment records are separate from fulfillment facts. A return arriving at
# the warehouse creates a refund intent; only a payment-provider fact settles it.
class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"
    __table_args__ = (
        CheckConstraint("status IN ('captured', 'partially_refunded', 'refunded')", name="ck_payment_transaction_status"),
        UniqueConstraint("provider", "provider_payment_id", name="uq_payment_provider_reference"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_payment_id: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    status: Mapped[str] = mapped_column(String(32), default="captured")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class RefundIntent(Base):
    __tablename__ = "refund_intents"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'submitted', 'succeeded', 'failed')", name="ck_refund_intent_status"),
        UniqueConstraint("case_id", name="uq_refund_intent_case"),
        UniqueConstraint("provider", "provider_refund_id", name="uq_refund_provider_reference"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("after_sales_cases.id"), index=True)
    payment_transaction_id: Mapped[str] = mapped_column(ForeignKey("payment_transactions.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    provider: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    provider_refund_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RefundAttempt(Base):
    __tablename__ = "refund_attempts"
    __table_args__ = (UniqueConstraint("refund_intent_id", "attempt_no", name="uq_refund_attempt_sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    refund_intent_id: Mapped[str] = mapped_column(ForeignKey("refund_intents.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    provider_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class PaymentProviderEvent(Base):
    __tablename__ = "payment_provider_events"
    __table_args__ = (UniqueConstraint("provider", "provider_event_id", name="uq_payment_provider_event"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_event_id: Mapped[str] = mapped_column(String(128))
    refund_intent_id: Mapped[str] = mapped_column(ForeignKey("refund_intents.id"), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PaymentReconciliationRun(Base):
    __tablename__ = "payment_reconciliation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="completed")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentReconciliationItem(Base):
    __tablename__ = "payment_reconciliation_items"
    __table_args__ = (UniqueConstraint("run_id", "refund_intent_id", name="uq_payment_reconcile_intent"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("payment_reconciliation_runs.id"), index=True)
    refund_intent_id: Mapped[str] = mapped_column(ForeignKey("refund_intents.id"), index=True)
    status: Mapped[str] = mapped_column(String(32))
    provider_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (UniqueConstraint("order_id", "sku", name="uq_order_item_sku"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(128))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    refunded_quantity: Mapped[int] = mapped_column(Integer, default=0)


class AfterSalesItem(Base):
    __tablename__ = "after_sales_items"
    __table_args__ = (UniqueConstraint("case_id", "order_item_id", name="uq_after_sales_case_item"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("after_sales_cases.id"), index=True)
    order_item_id: Mapped[str] = mapped_column(ForeignKey("order_items.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class InventoryStock(Base):
    __tablename__ = "inventory_stock"
    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    available_quantity: Mapped[int] = mapped_column(Integer, default=0)
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)


class InventoryReservation(Base):
    __tablename__ = "inventory_reservations"
    __table_args__ = (UniqueConstraint("case_id", name="uq_inventory_reservation_case"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("after_sales_cases.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
