"""Allow explicit Recovery evaluation outcomes.
Revision ID: 20261012_0030
Revises: 20260915_0029
"""
from alembic import op
import sqlalchemy as sa
revision = "20261012_0030"
down_revision = "20260915_0029"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("evaluation_results") as batch:
        batch.alter_column("status", existing_type=sa.String(16), type_=sa.String(32), existing_nullable=False)

def downgrade():
    with op.batch_alter_table("evaluation_results") as batch:
        batch.alter_column("status", existing_type=sa.String(32), type_=sa.String(16), existing_nullable=False)
