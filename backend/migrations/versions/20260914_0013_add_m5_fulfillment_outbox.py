"""Add reliable M5 fulfillment outbox, inbox and operational incidents.

Revision ID: 20260914_0013
Revises: 20260914_0012
Create Date: 2026-09-14 20:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260914_0013"
down_revision: Union[str, Sequence[str], None] = "20260914_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CASE_STATUS_CHECK = "status IN ('pending_confirmation', 'awaiting_pickup', 'pickup_scheduled', 'picked_up', 'return_received', 'refund_processing', 'replacement_shipped', 'fulfillment_exception', 'completed', 'cancelled', 'manual_review')"


def upgrade() -> None:
    op.drop_constraint("ck_after_sales_status", "after_sales_cases", type_="check")
    op.create_check_constraint("ck_after_sales_status", "after_sales_cases", CASE_STATUS_CHECK)
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("destination", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'processing', 'delivered', 'dead')", name="ck_outbox_event_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("aggregate_type", "aggregate_id", "event_type", "idempotency_key", name="uq_outbox_business_intent"),
    )
    op.create_index("ix_outbox_events_claim", "outbox_events", ["status", "next_attempt_at", "created_at"])
    op.create_table(
        "outbox_deliveries",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("outbox_event_id", sa.String(36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("provider_idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("status IN ('succeeded', 'failed')", name="ck_outbox_delivery_status"),
        sa.ForeignKeyConstraint(["outbox_event_id"], ["outbox_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("outbox_event_id", "attempt_no", name="uq_outbox_delivery_attempt"),
    )
    op.create_index("ix_outbox_deliveries_outbox_event_id", "outbox_deliveries", ["outbox_event_id"])
    op.create_table(
        "inbox_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_event_id", sa.String(128), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="received"),
        sa.Column("rejection_code", sa.String(64), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('received', 'applied', 'deferred', 'rejected')", name="ck_inbox_event_status"),
        sa.ForeignKeyConstraint(["case_id"], ["after_sales_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_inbox_provider_event"),
    )
    op.create_index("ix_inbox_events_deferred", "inbox_events", ["status", "received_at"])
    op.create_index("ix_inbox_events_case_sequence", "inbox_events", ["case_id", "sequence_no"])
    op.create_table(
        "fulfillment_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("inbox_event_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_event_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["case_id"], ["after_sales_cases.id"]),
        sa.ForeignKeyConstraint(["inbox_event_id"], ["inbox_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_fulfillment_provider_event"),
        sa.UniqueConstraint("case_id", "sequence_no", name="uq_fulfillment_case_sequence"),
        sa.UniqueConstraint("inbox_event_id"),
    )
    op.create_index("ix_fulfillment_events_case_created", "fulfillment_events", ["case_id", "created_at"])
    op.create_table(
        "customer_notifications",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False, server_default="in_app"),
        sa.Column("template", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_customer_notification_status"),
        sa.ForeignKeyConstraint(["case_id"], ["after_sales_cases.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "dedupe_key", name="uq_customer_notification_dedupe"),
    )
    op.create_index("ix_customer_notifications_case_created", "customer_notifications", ["case_id", "created_at"])
    op.create_index("ix_customer_notifications_user_id", "customer_notifications", ["user_id"])
    op.create_table(
        "fulfillment_incidents",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("inbox_event_id", sa.String(36), nullable=True),
        sa.Column("incident_type", sa.String(64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("assigned_operator_id", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("status IN ('open', 'acknowledged', 'resolved')", name="ck_fulfillment_incident_status"),
        sa.ForeignKeyConstraint(["assigned_operator_id"], ["actors.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["after_sales_cases.id"]),
        sa.ForeignKeyConstraint(["inbox_event_id"], ["inbox_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "dedupe_key", name="uq_fulfillment_incident_dedupe"),
    )
    op.create_index("ix_fulfillment_incidents_queue", "fulfillment_incidents", ["status", "created_at"])
    op.create_index("ix_fulfillment_incidents_case_id", "fulfillment_incidents", ["case_id"])


def downgrade() -> None:
    op.drop_table("fulfillment_incidents")
    op.drop_table("customer_notifications")
    op.drop_table("fulfillment_events")
    op.drop_table("inbox_events")
    op.drop_table("outbox_deliveries")
    op.drop_table("outbox_events")
    op.drop_constraint("ck_after_sales_status", "after_sales_cases", type_="check")
    op.create_check_constraint("ck_after_sales_status", "after_sales_cases", "status IN ('pending_confirmation', 'awaiting_pickup', 'pickup_scheduled', 'completed', 'cancelled', 'manual_review')")
