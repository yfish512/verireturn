"""Make published M6 alert rules historically immutable.

Revision ID: 20260914_0017
Revises: 20260914_0016
Create Date: 2026-09-14 23:30:00
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260914_0017"
down_revision: Union[str, Sequence[str], None] = "20260914_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION enforce_alert_rule_version_immutability() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'draft' THEN
              RAISE EXCEPTION 'published or retired alert rule versions cannot be deleted';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.status = 'retired' THEN
            RAISE EXCEPTION 'retired alert rule versions are immutable';
          END IF;
          IF OLD.status = 'published' THEN
            IF NEW.status <> 'retired'
              OR NEW.id IS DISTINCT FROM OLD.id
              OR NEW.rule_key IS DISTINCT FROM OLD.rule_key
              OR NEW.version IS DISTINCT FROM OLD.version
              OR NEW.metric_name IS DISTINCT FROM OLD.metric_name
              OR NEW.comparison IS DISTINCT FROM OLD.comparison
              OR NEW.threshold IS DISTINCT FROM OLD.threshold
              OR NEW.severity IS DISTINCT FROM OLD.severity
              OR NEW.created_by IS DISTINCT FROM OLD.created_by
              OR NEW.created_at IS DISTINCT FROM OLD.created_at
              OR NEW.published_at IS DISTINCT FROM OLD.published_at THEN
              RAISE EXCEPTION 'published alert rule configuration is immutable; it may only be retired';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER alert_rule_versions_immutable
        BEFORE UPDATE OR DELETE ON alert_rule_versions
        FOR EACH ROW EXECUTE FUNCTION enforce_alert_rule_version_immutability();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER alert_rule_versions_immutable ON alert_rule_versions")
    op.execute("DROP FUNCTION enforce_alert_rule_version_immutability()")
