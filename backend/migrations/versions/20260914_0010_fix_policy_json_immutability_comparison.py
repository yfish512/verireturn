"""Use text comparison for PostgreSQL JSON policy payloads.

Revision ID: 20260914_0010
Revises: 20260914_0009
Create Date: 2026-09-14 13:30:00
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260914_0010"
down_revision: Union[str, Sequence[str], None] = "20260914_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION prevent_policy_version_mutation()
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
"""


def upgrade() -> None:
    op.execute(FUNCTION_SQL)


def downgrade() -> None:
    op.execute(FUNCTION_SQL)
