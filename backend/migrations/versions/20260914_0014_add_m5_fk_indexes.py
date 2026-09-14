"""Add ORM-declared M5 foreign-key lookup indexes.

Revision ID: 20260914_0014
Revises: 20260914_0013
Create Date: 2026-09-14 20:40:00
"""
from typing import Sequence, Union
from alembic import op

revision: str = "20260914_0014"
down_revision: Union[str, Sequence[str], None] = "20260914_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_customer_notifications_case_id", "customer_notifications", ["case_id"])
    op.create_index("ix_fulfillment_events_case_id", "fulfillment_events", ["case_id"])
    op.create_index("ix_inbox_events_case_id", "inbox_events", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_inbox_events_case_id", table_name="inbox_events")
    op.drop_index("ix_fulfillment_events_case_id", table_name="fulfillment_events")
    op.drop_index("ix_customer_notifications_case_id", table_name="customer_notifications")
