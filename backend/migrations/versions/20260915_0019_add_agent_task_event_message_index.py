"""Index task-memory event message references.

Revision ID: 20260915_0019
Revises: 20260915_0018
"""
from typing import Sequence, Union
from alembic import op

revision: str = "20260915_0019"
down_revision: Union[str, Sequence[str], None] = "20260915_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_agent_task_events_message_id", "agent_task_events", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_task_events_message_id", table_name="agent_task_events")
