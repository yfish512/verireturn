"""Allow production finance and review roles.
Revision ID: 20260915_0025
Revises: 20260915_0024
"""
from alembic import op
revision='20260915_0025'; down_revision='20260915_0024'; branch_labels=None; depends_on=None
def upgrade():
    # The original named check is portable on PostgreSQL; SQLite metadata tests build fresh tables.
    op.drop_constraint('ck_actor_role', 'actors', type_='check')
    op.create_check_constraint('ck_actor_role', 'actors', "role IN ('customer','operator','ops_manager','finance','reviewer','internal_service')")
def downgrade():
    op.drop_constraint('ck_actor_role','actors',type_='check')
    op.create_check_constraint('ck_actor_role','actors', "role IN ('customer','operator','ops_manager','internal_service')")
