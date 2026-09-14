"""Add M1 state-machine audit and generic idempotency guards.

Revision ID: 20260913_0003
Revises: 20260913_0002
Create Date: 2026-09-13 00:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260913_0003"
down_revision: Union[str, Sequence[str], None] = "20260913_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_after_sales_idempotency_key", "after_sales_cases", type_="unique")
    op.add_column(
        "after_sales_cases",
        sa.Column("operation", sa.String(length=64), nullable=False, server_default="create_after_sales_case"),
    )
    op.add_column("after_sales_cases", sa.Column("request_hash", sa.String(length=64), nullable=False, server_default="legacy"))
    op.add_column(
        "after_sales_cases",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.add_column("after_sales_cases", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_after_sales_request_type", "after_sales_cases", "request_type IN ('refund', 'exchange')")
    op.create_check_constraint(
        "ck_after_sales_status",
        "after_sales_cases",
        "status IN ('pending_confirmation', 'awaiting_pickup', 'pickup_scheduled', 'completed', 'cancelled', 'manual_review')",
    )
    op.create_index("ix_after_sales_cases_user_status_created_at", "after_sales_cases", ["user_id", "status", "created_at"])

    op.add_column("audit_logs", sa.Column("actor_type", sa.String(length=32), nullable=False, server_default="system"))
    op.add_column("audit_logs", sa.Column("actor_id", sa.String(length=64), nullable=True))
    op.add_column("audit_logs", sa.Column("request_id", sa.String(length=64), nullable=True))

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_id", "operation", "idempotency_key", name="uq_idempotency_actor_operation_key"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_records")
    op.drop_column("audit_logs", "request_id")
    op.drop_column("audit_logs", "actor_id")
    op.drop_column("audit_logs", "actor_type")
    op.drop_index("ix_after_sales_cases_user_status_created_at", table_name="after_sales_cases")
    op.drop_constraint("ck_after_sales_status", "after_sales_cases", type_="check")
    op.drop_constraint("ck_after_sales_request_type", "after_sales_cases", type_="check")
    op.drop_column("after_sales_cases", "completed_at")
    op.drop_column("after_sales_cases", "updated_at")
    op.drop_column("after_sales_cases", "request_hash")
    op.drop_column("after_sales_cases", "operation")
    op.create_unique_constraint("uq_after_sales_idempotency_key", "after_sales_cases", ["idempotency_key"])
