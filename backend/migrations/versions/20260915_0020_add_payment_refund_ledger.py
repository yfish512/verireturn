"""Add durable payment and refund ledger.

Revision ID: 20260915_0020
Revises: 20260915_0019
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20260915_0020"
down_revision: Union[str, Sequence[str], None] = "20260915_0019"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("payment_transactions", sa.Column("id", sa.String(36), primary_key=True), sa.Column("order_id", sa.String(32), sa.ForeignKey("orders.id"), nullable=False, unique=True), sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False), sa.Column("provider", sa.String(32), nullable=False), sa.Column("provider_payment_id", sa.String(128), nullable=False), sa.Column("amount", sa.Numeric(10, 2), nullable=False), sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"), sa.Column("status", sa.String(32), nullable=False, server_default="captured"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.CheckConstraint("status IN ('captured', 'partially_refunded', 'refunded')", name="ck_payment_transaction_status"), sa.UniqueConstraint("provider", "provider_payment_id", name="uq_payment_provider_reference"))
    op.create_index("ix_payment_transactions_user_id", "payment_transactions", ["user_id"])
    op.create_table("refund_intents", sa.Column("id", sa.String(36), primary_key=True), sa.Column("case_id", sa.Integer(), sa.ForeignKey("after_sales_cases.id"), nullable=False), sa.Column("payment_transaction_id", sa.String(36), sa.ForeignKey("payment_transactions.id"), nullable=False), sa.Column("amount", sa.Numeric(10, 2), nullable=False), sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"), sa.Column("provider", sa.String(32), nullable=False), sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True), sa.Column("status", sa.String(16), nullable=False, server_default="pending"), sa.Column("provider_refund_id", sa.String(128)), sa.Column("failure_code", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("settled_at", sa.DateTime(timezone=True)), sa.CheckConstraint("status IN ('pending', 'submitted', 'succeeded', 'failed')", name="ck_refund_intent_status"), sa.UniqueConstraint("case_id", name="uq_refund_intent_case"), sa.UniqueConstraint("provider", "provider_refund_id", name="uq_refund_provider_reference"))
    op.create_index("ix_refund_intents_status", "refund_intents", ["status"])
    op.create_table("refund_attempts", sa.Column("id", sa.String(36), primary_key=True), sa.Column("refund_intent_id", sa.String(36), sa.ForeignKey("refund_intents.id"), nullable=False), sa.Column("attempt_no", sa.Integer(), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("provider_response", sa.JSON()), sa.Column("error_code", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("refund_intent_id", "attempt_no", name="uq_refund_attempt_sequence"))
    op.create_index("ix_refund_attempts_refund_intent_id", "refund_attempts", ["refund_intent_id"])

def downgrade() -> None:
    op.drop_table("refund_attempts"); op.drop_table("refund_intents"); op.drop_table("payment_transactions")
