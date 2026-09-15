"""Add explicit customer conversation task memory.

Revision ID: 20260915_0018
Revises: 20260914_0017
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20260915_0018"
down_revision: Union[str, Sequence[str], None] = "20260914_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("agent_threads", sa.Column("id", sa.String(64), primary_key=True), sa.Column("actor_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False), sa.Column("status", sa.String(16), nullable=False, server_default="active"), sa.Column("memory_version", sa.Integer(), nullable=False, server_default="1"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_agent_threads_actor_id", "agent_threads", ["actor_id"])
    op.create_table("agent_message_records", sa.Column("id", sa.String(36), primary_key=True), sa.Column("thread_id", sa.String(64), sa.ForeignKey("agent_threads.id"), nullable=False), sa.Column("sequence_no", sa.Integer(), nullable=False), sa.Column("role", sa.String(16), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("client_message_id", sa.String(64)), sa.Column("reply_to_message_id", sa.String(36)), sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id")), sa.Column("payload_json", sa.JSON()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.CheckConstraint("role IN ('customer', 'agent')", name="ck_agent_message_record_role"), sa.UniqueConstraint("thread_id", "client_message_id", name="uq_agent_message_client_id"), sa.UniqueConstraint("thread_id", "sequence_no", name="uq_agent_message_sequence"))
    op.create_index("ix_agent_message_records_thread_id", "agent_message_records", ["thread_id"])
    op.create_index("ix_agent_message_records_reply_to_message_id", "agent_message_records", ["reply_to_message_id"])
    op.create_index("ix_agent_message_records_run_id", "agent_message_records", ["run_id"])
    op.create_table("agent_tasks", sa.Column("id", sa.String(36), primary_key=True), sa.Column("thread_id", sa.String(64), sa.ForeignKey("agent_threads.id"), nullable=False, unique=True), sa.Column("intent", sa.String(64), nullable=False), sa.Column("phase", sa.String(48), nullable=False), sa.Column("slots_json", sa.JSON(), nullable=False), sa.Column("missing_slots", sa.JSON(), nullable=False), sa.Column("active_case_id", sa.Integer(), sa.ForeignKey("after_sales_cases.id")), sa.Column("version", sa.Integer(), nullable=False, server_default="1"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.CheckConstraint("phase IN ('collecting_slots', 'ready_to_execute', 'awaiting_customer_confirmation', 'awaiting_pickup_slot', 'completed', 'cancelled', 'expired')", name="ck_agent_task_phase"))
    op.create_index("ix_agent_tasks_active_case_id", "agent_tasks", ["active_case_id"])
    op.create_table("agent_task_events", sa.Column("id", sa.String(36), primary_key=True), sa.Column("task_id", sa.String(36), sa.ForeignKey("agent_tasks.id"), nullable=False), sa.Column("sequence_no", sa.Integer(), nullable=False), sa.Column("event_type", sa.String(64), nullable=False), sa.Column("payload_json", sa.JSON(), nullable=False), sa.Column("message_id", sa.String(36), sa.ForeignKey("agent_message_records.id")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("task_id", "sequence_no", name="uq_agent_task_event_sequence"))
    op.create_index("ix_agent_task_events_task_id", "agent_task_events", ["task_id"])
    op.execute("""
        CREATE FUNCTION enforce_agent_task_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'agent task events are append-only';
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER agent_task_events_append_only
        BEFORE UPDATE OR DELETE ON agent_task_events
        FOR EACH ROW EXECUTE FUNCTION enforce_agent_task_events_append_only();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER agent_task_events_append_only ON agent_task_events")
    op.execute("DROP FUNCTION enforce_agent_task_events_append_only()")
    op.drop_table("agent_task_events")
    op.drop_table("agent_tasks")
    op.drop_table("agent_message_records")
    op.drop_table("agent_threads")
