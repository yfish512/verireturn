"""Add controlled review upload metadata.
Revision ID: 20260915_0029
Revises: 20260915_0028
"""
from alembic import op
import sqlalchemy as sa
revision='20260915_0029'; down_revision='20260915_0028'; branch_labels=None; depends_on=None
def upgrade():
    op.create_table('review_uploads', sa.Column('id',sa.String(36),primary_key=True),sa.Column('uploaded_by',sa.String(64),nullable=False),sa.Column('object_ref',sa.String(256),nullable=False,unique=True),sa.Column('content_hash',sa.String(64),nullable=False),sa.Column('media_type',sa.String(128),nullable=False),sa.Column('byte_size',sa.Integer(),nullable=False),sa.Column('status',sa.String(16),nullable=False,server_default='ready'),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_review_uploads_uploaded_by','review_uploads',['uploaded_by'])
def downgrade(): op.drop_table('review_uploads')
