"""Complete P0-P2 task, exchange, privacy and RBAC foundations.
Revision ID: 20260915_0024
Revises: 20260915_0023
"""
from alembic import op
import sqlalchemy as sa
revision='20260915_0024'; down_revision='20260915_0023'; branch_labels=None; depends_on=None

def upgrade():
    with op.batch_alter_table('agent_tasks') as b:
        try: b.drop_constraint('agent_tasks_thread_id_key', type_='unique')
        except Exception: pass
        b.add_column(sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
        b.create_index('ix_agent_tasks_thread_id', ['thread_id'])
    with op.batch_alter_table('agent_threads') as b:
        b.add_column(sa.Column('focus_task_id', sa.String(36), nullable=True))
        b.create_index('ix_agent_threads_focus_task_id', ['focus_task_id'])
    op.create_table('exchange_fulfillments', sa.Column('id',sa.String(36),primary_key=True), sa.Column('case_id',sa.Integer(),sa.ForeignKey('after_sales_cases.id'),nullable=False), sa.Column('replacement_sku',sa.String(64),nullable=False), sa.Column('quantity',sa.Integer(),nullable=False), sa.Column('reservation_id',sa.String(36),sa.ForeignKey('inventory_reservations.id')), sa.Column('tracking_number',sa.String(64)), sa.Column('status',sa.String(16),nullable=False,server_default='pending'), sa.Column('failure_code',sa.String(64)), sa.Column('version',sa.Integer(),nullable=False,server_default='1'), sa.Column('created_at',sa.DateTime(timezone=True),nullable=False), sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False), sa.UniqueConstraint('case_id',name='uq_exchange_fulfillment_case'), sa.CheckConstraint("status IN ('pending','allocated','shipped','delivered','failed','cancelled')",name='ck_exchange_fulfillment_status'))
    op.create_index('ix_exchange_fulfillments_case_id','exchange_fulfillments',['case_id'])
    op.create_table('review_attachments',sa.Column('id',sa.String(36),primary_key=True),sa.Column('ticket_id',sa.String(36),sa.ForeignKey('review_tickets.id'),nullable=False),sa.Column('uploaded_by',sa.String(64),nullable=False),sa.Column('object_ref',sa.String(256),nullable=False),sa.Column('content_hash',sa.String(64),nullable=False),sa.Column('media_type',sa.String(128),nullable=False),sa.Column('version',sa.Integer(),nullable=False,server_default='1'),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('ticket_id','content_hash',name='uq_review_attachment_hash'))
    op.create_index('ix_review_attachments_ticket_id','review_attachments',['ticket_id'])
    op.create_table('privacy_requests',sa.Column('id',sa.String(36),primary_key=True),sa.Column('user_id',sa.String(32),sa.ForeignKey('users.id'),nullable=False),sa.Column('request_type',sa.String(16),nullable=False),sa.Column('status',sa.String(16),nullable=False,server_default='pending'),sa.Column('result_json',sa.JSON()),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('completed_at',sa.DateTime(timezone=True)),sa.CheckConstraint("request_type IN ('export','delete')",name='ck_privacy_request_type'))
    op.create_index('ix_privacy_requests_user_id','privacy_requests',['user_id'])

def downgrade():
    op.drop_table('privacy_requests'); op.drop_table('review_attachments'); op.drop_table('exchange_fulfillments')
    with op.batch_alter_table('agent_threads') as b: b.drop_index('ix_agent_threads_focus_task_id'); b.drop_column('focus_task_id')
    with op.batch_alter_table('agent_tasks') as b: b.drop_index('ix_agent_tasks_thread_id'); b.drop_column('archived_at')
