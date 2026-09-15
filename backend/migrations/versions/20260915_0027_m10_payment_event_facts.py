"""Store payment callback financial facts for M10 reconciliation.
Revision ID: 20260915_0027
Revises: 20260915_0026
"""
from alembic import op
import sqlalchemy as sa
revision='20260915_0027'; down_revision='20260915_0026'; branch_labels=None; depends_on=None
def upgrade():
    with op.batch_alter_table('payment_provider_events') as b:
        b.add_column(sa.Column('amount', sa.Numeric(10,2), nullable=True))
        b.add_column(sa.Column('currency', sa.String(3), nullable=True))
        b.add_column(sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column('signature_key_id', sa.String(64), nullable=True))
def downgrade():
    with op.batch_alter_table('payment_provider_events') as b:
        b.drop_column('signature_key_id'); b.drop_column('occurred_at'); b.drop_column('currency'); b.drop_column('amount')
