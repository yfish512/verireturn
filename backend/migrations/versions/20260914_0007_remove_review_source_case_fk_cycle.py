"""Remove the review/source-case FK cycle.

Revision ID: 20260914_0007
Revises: 20260914_0006
Create Date: 2026-09-14 12:20:00
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260914_0007"
down_revision: Union[str, Sequence[str], None] = "20260914_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("review_tickets_source_case_id_fkey", "review_tickets", type_="foreignkey")


def downgrade() -> None:
    op.create_foreign_key("review_tickets_source_case_id_fkey", "review_tickets", "after_sales_cases", ["source_case_id"], ["id"])
