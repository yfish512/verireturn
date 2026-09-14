"""Store business times as UTC-aware timestamps.

Revision ID: 20260913_0002
Revises: 20260913_0001
Create Date: 2026-09-13 00:10:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260913_0002"
down_revision: Union[str, Sequence[str], None] = "20260913_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing MVP data was already interpreted as UTC. PostgreSQL needs the
    # USING expression so it preserves the instant rather than the wall clock.
    for table_name, column_name in (
        ("orders", "delivered_at"),
        ("after_sales_cases", "created_at"),
        ("audit_logs", "created_at"),
    ):
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.DateTime(),
            type_=sa.DateTime(timezone=True),
            postgresql_using=f"{column_name} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    for table_name, column_name in (
        ("audit_logs", "created_at"),
        ("after_sales_cases", "created_at"),
        ("orders", "delivered_at"),
    ):
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(),
            postgresql_using=f"{column_name} AT TIME ZONE 'UTC'",
        )
