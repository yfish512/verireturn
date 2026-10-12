"""Explicit customer resolution and restock task phases."""
from alembic import op
revision='20261012_0032'
down_revision='14addcc6afc0'
branch_labels=None
depends_on=None

def upgrade():
    with op.batch_alter_table('agent_tasks') as batch:
        batch.drop_constraint('ck_agent_task_phase',type_='check')
        batch.create_check_constraint('ck_agent_task_phase',"phase IN ('collecting_slots', 'ready_to_execute', 'awaiting_customer_confirmation', 'awaiting_pickup_slot', 'awaiting_resolution', 'waiting_stock', 'completed', 'cancelled', 'expired')")

def downgrade():
    op.execute("UPDATE agent_tasks SET phase='ready_to_execute' WHERE phase IN ('awaiting_resolution','waiting_stock')")
    with op.batch_alter_table('agent_tasks') as batch:
        batch.drop_constraint('ck_agent_task_phase',type_='check')
        batch.create_check_constraint('ck_agent_task_phase',"phase IN ('collecting_slots', 'ready_to_execute', 'awaiting_customer_confirmation', 'awaiting_pickup_slot', 'completed', 'cancelled', 'expired')")
