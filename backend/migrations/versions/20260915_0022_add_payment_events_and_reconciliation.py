"""Add payment callback inbox and reconciliation records.

Revision ID: 20260915_0022
Revises: 20260915_0021
"""
from alembic import op
import sqlalchemy as sa
revision = "20260915_0022"
down_revision = "20260915_0021"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("payment_provider_events", sa.Column("id", sa.String(36), primary_key=True), sa.Column("provider", sa.String(32), nullable=False), sa.Column("provider_event_id", sa.String(128), nullable=False), sa.Column("refund_intent_id", sa.String(36), sa.ForeignKey("refund_intents.id"), nullable=False), sa.Column("payload_hash", sa.String(64), nullable=False), sa.Column("outcome", sa.String(16), nullable=False), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("received_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("provider", "provider_event_id", name="uq_payment_provider_event"))
    op.create_index("ix_payment_provider_events_refund_intent_id", "payment_provider_events", ["refund_intent_id"])
    op.create_table("payment_reconciliation_runs", sa.Column("id", sa.String(36), primary_key=True), sa.Column("provider", sa.String(32), nullable=False), sa.Column("status", sa.String(16), nullable=False, server_default="completed"), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_payment_reconciliation_runs_provider", "payment_reconciliation_runs", ["provider"])
    op.create_table("payment_reconciliation_items", sa.Column("id", sa.String(36), primary_key=True), sa.Column("run_id", sa.String(36), sa.ForeignKey("payment_reconciliation_runs.id"), nullable=False), sa.Column("refund_intent_id", sa.String(36), sa.ForeignKey("refund_intents.id"), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("provider_status", sa.String(32)), sa.Column("detail", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("run_id", "refund_intent_id", name="uq_payment_reconcile_intent"))
    op.create_index("ix_payment_reconciliation_items_run_id", "payment_reconciliation_items", ["run_id"])
    op.create_index("ix_payment_reconciliation_items_refund_intent_id", "payment_reconciliation_items", ["refund_intent_id"])

def downgrade() -> None:
    op.drop_table("payment_reconciliation_items"); op.drop_table("payment_reconciliation_runs"); op.drop_table("payment_provider_events")
