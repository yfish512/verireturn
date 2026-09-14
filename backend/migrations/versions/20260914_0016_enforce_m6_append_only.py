"""Make M6 operational evidence append-only in PostgreSQL.

Revision ID: 20260914_0016
Revises: 20260914_0015
Create Date: 2026-09-14 23:00:00
"""
from typing import Sequence, Union
from alembic import op

revision: str = "20260914_0016"
down_revision: Union[str, Sequence[str], None] = "20260914_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION prevent_m6_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'M6 operational evidence is append-only'; END;
        $$;
    """)
    for table in ("ops_alert_events", "evaluation_results"):
        op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION prevent_m6_evidence_mutation();")


def downgrade() -> None:
    for table in ("ops_alert_events", "evaluation_results"):
        op.execute(f"DROP TRIGGER {table}_append_only ON {table}")
    op.execute("DROP FUNCTION prevent_m6_evidence_mutation()")
