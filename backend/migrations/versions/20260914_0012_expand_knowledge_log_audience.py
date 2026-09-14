"""Allow a complete permission scope in knowledge retrieval audit logs.

Revision ID: 20260914_0012
Revises: 20260914_0011
Create Date: 2026-09-14 18:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260914_0012"
down_revision: Union[str, Sequence[str], None] = "20260914_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("knowledge_retrieval_logs", "audience", existing_type=sa.String(length=16), type_=sa.String(length=64), existing_nullable=False)


def downgrade() -> None:
    op.alter_column("knowledge_retrieval_logs", "audience", existing_type=sa.String(length=64), type_=sa.String(length=16), existing_nullable=False)
