"""Add structured pickup appointments and expiring inventory holds.
Revision ID: 20260915_0028
Revises: 20260915_0027
"""
from alembic import op
import sqlalchemy as sa
revision='20260915_0028'; down_revision='20260915_0027'; branch_labels=None; depends_on=None
def upgrade():
    with op.batch_alter_table('inventory_reservations') as b:
        b.add_column(sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column('released_at', sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column('release_reason', sa.String(64), nullable=True))
        b.create_index('ix_inventory_reservations_expires_at', ['expires_at'])
    op.create_table('pickup_slot_capacities', sa.Column('id',sa.String(36),primary_key=True),sa.Column('provider',sa.String(32),nullable=False),sa.Column('start_at',sa.DateTime(timezone=True),nullable=False),sa.Column('end_at',sa.DateTime(timezone=True),nullable=False),sa.Column('capacity',sa.Integer(),nullable=False,server_default='20'),sa.Column('reserved_quantity',sa.Integer(),nullable=False,server_default='0'),sa.Column('version',sa.Integer(),nullable=False,server_default='1'),sa.UniqueConstraint('provider','start_at','end_at',name='uq_pickup_slot_capacity'))
    op.create_table('pickup_appointments',sa.Column('id',sa.String(36),primary_key=True),sa.Column('case_id',sa.Integer(),sa.ForeignKey('after_sales_cases.id'),nullable=False),sa.Column('provider',sa.String(32),nullable=False,server_default='demo_fulfillment'),sa.Column('start_at',sa.DateTime(timezone=True),nullable=False),sa.Column('end_at',sa.DateTime(timezone=True),nullable=False),sa.Column('timezone_name',sa.String(64),nullable=False,server_default='Asia/Shanghai'),sa.Column('display_text',sa.String(64),nullable=False),sa.Column('provider_appointment_id',sa.String(128)),sa.Column('status',sa.String(16),nullable=False,server_default='scheduled'),sa.Column('version',sa.Integer(),nullable=False,server_default='1'),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('case_id',name='uq_pickup_appointment_case'),sa.CheckConstraint("status IN ('scheduled','cancelled','expired')",name='ck_pickup_appointment_status'))
    op.create_index('ix_pickup_appointments_case_id','pickup_appointments',['case_id'])
    op.create_index('ix_pickup_appointments_slot','pickup_appointments',['provider','start_at','end_at','status'])
def downgrade():
    op.drop_table('pickup_appointments');op.drop_table('pickup_slot_capacities')
    with op.batch_alter_table('inventory_reservations') as b:
        b.drop_index('ix_inventory_reservations_expires_at');b.drop_column('release_reason');b.drop_column('released_at');b.drop_column('expires_at')
