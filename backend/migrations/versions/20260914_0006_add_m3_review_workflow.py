"""Add M3 policy versions and append-only review workflow.

Revision ID: 20260914_0006
Revises: 20260914_0005
Create Date: 2026-09-14 12:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260914_0006"
down_revision: Union[str, Sequence[str], None] = "20260914_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "actors",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("role IN ('customer', 'operator', 'ops_manager', 'internal_service')", name="ck_actor_role"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "policy_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rules_json", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("published_by", sa.String(length=64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('draft', 'published', 'retired')", name="ck_policy_version_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version", name="uq_policy_versions_version"),
    )
    op.create_table(
        "review_tickets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("requester_id", sa.String(length=32), nullable=False),
        sa.Column("order_id", sa.String(length=32), nullable=False),
        sa.Column("source_case_id", sa.Integer(), nullable=True),
        sa.Column("source_run_id", sa.String(length=36), nullable=True),
        sa.Column("request_type", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("request_snapshot", sa.JSON(), nullable=False),
        sa.Column("trigger_code", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="normal"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("assigned_operator_id", sa.String(length=64), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("policy_version_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("request_type IN ('refund', 'exchange')", name="ck_review_ticket_request_type"),
        sa.CheckConstraint("priority IN ('normal', 'high')", name="ck_review_ticket_priority"),
        sa.CheckConstraint("status IN ('open', 'claimed', 'waiting_customer', 'approved', 'rejected', 'closed', 'expired')", name="ck_review_ticket_status"),
        sa.ForeignKeyConstraint(["assigned_operator_id"], ["actors.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["policy_version_id"], ["policy_versions.id"]),
        sa.ForeignKeyConstraint(["requester_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_case_id"], ["after_sales_cases.id"]),
        sa.ForeignKeyConstraint(["source_run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_tickets_requester_id", "review_tickets", ["requester_id"])
    op.create_index("ix_review_tickets_order_id", "review_tickets", ["order_id"])
    op.create_index("ix_review_tickets_source_case_id", "review_tickets", ["source_case_id"])
    op.create_index("ix_review_tickets_source_run_id", "review_tickets", ["source_run_id"])
    op.create_index("ix_review_tickets_status", "review_tickets", ["status"])
    op.create_index("ix_review_tickets_assigned_operator_id", "review_tickets", ["assigned_operator_id"])
    op.create_index("ix_review_tickets_due_at", "review_tickets", ["due_at"])
    op.create_index("ix_review_tickets_policy_version_id", "review_tickets", ["policy_version_id"])
    op.create_index("ix_review_tickets_queue", "review_tickets", ["status", "priority", "due_at", "created_at"])
    op.create_index("ix_review_tickets_assignee", "review_tickets", ["assigned_operator_id", "status", "updated_at"])
    op.create_index("ix_review_tickets_requester_created", "review_tickets", ["requester_id", "created_at"])
    op.create_table(
        "review_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["review_tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticket_id", "sequence_no", name="uq_review_event_sequence"),
    )
    op.create_index("ix_review_events_ticket_id", "review_events", ["ticket_id"])
    op.add_column("after_sales_cases", sa.Column("policy_version_id", sa.String(length=36), nullable=True))
    op.add_column("after_sales_cases", sa.Column("source_review_ticket_id", sa.String(length=36), nullable=True))
    op.create_foreign_key("fk_after_sales_cases_policy_version", "after_sales_cases", "policy_versions", ["policy_version_id"], ["id"])
    op.create_foreign_key("fk_after_sales_cases_source_review_ticket", "after_sales_cases", "review_tickets", ["source_review_ticket_id"], ["id"])
    op.create_unique_constraint("uq_after_sales_cases_source_review_ticket", "after_sales_cases", ["source_review_ticket_id"])
    op.execute("""
        CREATE FUNCTION prevent_review_event_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'review_events is append-only';
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER review_events_append_only
        BEFORE UPDATE OR DELETE ON review_events
        FOR EACH ROW EXECUTE FUNCTION prevent_review_event_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER review_events_append_only ON review_events")
    op.execute("DROP FUNCTION prevent_review_event_mutation()")
    op.drop_constraint("uq_after_sales_cases_source_review_ticket", "after_sales_cases", type_="unique")
    op.drop_constraint("fk_after_sales_cases_source_review_ticket", "after_sales_cases", type_="foreignkey")
    op.drop_constraint("fk_after_sales_cases_policy_version", "after_sales_cases", type_="foreignkey")
    op.drop_column("after_sales_cases", "source_review_ticket_id")
    op.drop_column("after_sales_cases", "policy_version_id")
    op.drop_table("review_events")
    op.drop_table("review_tickets")
    op.drop_table("policy_versions")
    op.drop_table("actors")
