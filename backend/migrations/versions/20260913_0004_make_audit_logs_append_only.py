"""Enforce append-only audit logs in PostgreSQL.

Revision ID: 20260913_0004
Revises: 20260913_0003
Create Date: 2026-09-13 00:45:00
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260913_0004"
down_revision: Union[str, Sequence[str], None] = "20260913_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION prevent_audit_log_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only';
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_logs_append_only ON audit_logs")
    op.execute("DROP FUNCTION prevent_audit_log_mutation()")
