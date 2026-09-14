"""Enforce one published immutable policy version.

Revision ID: 20260914_0009
Revises: 20260914_0008
Create Date: 2026-09-14 13:20:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260914_0009"
down_revision: Union[str, Sequence[str], None] = "20260914_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_policy_versions_single_published", "policy_versions", ["status"], unique=True,
        postgresql_where=sa.text("status = 'published'"), sqlite_where=sa.text("status = 'published'"),
    )
    op.execute("""
        CREATE FUNCTION prevent_policy_version_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'policy_versions cannot be deleted';
            END IF;
            IF NEW.rules_json::text IS DISTINCT FROM OLD.rules_json::text
               OR NEW.checksum IS DISTINCT FROM OLD.checksum
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.published_by IS DISTINCT FROM OLD.published_by
               OR NEW.published_at IS DISTINCT FROM OLD.published_at THEN
                RAISE EXCEPTION 'published policy content is immutable';
            END IF;
            IF OLD.status <> 'published' OR NEW.status <> 'retired' THEN
                RAISE EXCEPTION 'a policy version may only transition from published to retired';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER policy_versions_immutable
        BEFORE UPDATE OR DELETE ON policy_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_policy_version_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER policy_versions_immutable ON policy_versions")
    op.execute("DROP FUNCTION prevent_policy_version_mutation()")
    op.drop_index("uq_policy_versions_single_published", table_name="policy_versions")
