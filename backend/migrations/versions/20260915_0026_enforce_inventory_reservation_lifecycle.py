"""Enforce inventory reservation lifecycle.
Revision ID: 20260915_0026
Revises: 20260915_0025
"""
from alembic import op
revision='20260915_0026'; down_revision='20260915_0025'; branch_labels=None; depends_on=None
def upgrade():
    op.create_check_constraint('ck_inventory_reservation_status','inventory_reservations',"status IN ('reserved','released','consumed')")
def downgrade(): op.drop_constraint('ck_inventory_reservation_status','inventory_reservations',type_='check')
