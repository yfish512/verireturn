"""Add M2 Agent runs, tool traces and confirmations.

Revision ID: 20260914_0005
Revises: 20260913_0004
Create Date: 2026-09-14 09:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260914_0005"
down_revision: Union[str, Sequence[str], None] = "20260913_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("final_response", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_thread_id", "agent_runs", ["thread_id"])
    op.create_index("ix_agent_runs_actor_id", "agent_runs", ["actor_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("graph_node", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("arguments_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("m1_request_id", sa.String(length=64), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence_no", name="uq_agent_tool_call_sequence"),
    )
    op.create_index("ix_agent_tool_calls_run_id", "agent_tool_calls", ["run_id"])
    op.create_table(
        "agent_confirmations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=32), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["after_sales_cases.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_confirmations_thread_id", "agent_confirmations", ["thread_id"])
    op.create_index("ix_agent_confirmations_run_id", "agent_confirmations", ["run_id"])
    op.create_index("ix_agent_confirmations_actor_id", "agent_confirmations", ["actor_id"])
    op.create_index("ix_agent_confirmations_case_id", "agent_confirmations", ["case_id"])
    op.create_index("ix_agent_confirmations_status", "agent_confirmations", ["status"])


def downgrade() -> None:
    op.drop_table("agent_confirmations")
    op.drop_table("agent_tool_calls")
    op.drop_table("agent_runs")
