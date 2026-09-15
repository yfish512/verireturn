"""Add payment ledger lookup indexes.

Revision ID: 20260915_0021
Revises: 20260915_0020
"""
from alembic import op

revision = "20260915_0021"
down_revision = "20260915_0020"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_index("ix_refund_intents_case_id", "refund_intents", ["case_id"])
    op.create_index("ix_refund_intents_payment_transaction_id", "refund_intents", ["payment_transaction_id"])

def downgrade() -> None:
    op.drop_index("ix_refund_intents_payment_transaction_id", table_name="refund_intents")
    op.drop_index("ix_refund_intents_case_id", table_name="refund_intents")
