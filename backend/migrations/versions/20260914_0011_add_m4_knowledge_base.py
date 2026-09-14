"""Add M4 versioned, permission-scoped knowledge base and pgvector storage.

Revision ID: 20260914_0011
Revises: 20260914_0010
Create Date: 2026-09-14 17:10:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "20260914_0011"
down_revision: Union[str, Sequence[str], None] = "20260914_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL 18 in the Miniforge environment ships this extension. It must
    # be enabled per database; the Python package alone cannot create VECTOR.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("stable_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("audience", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("audience IN ('customer', 'operator', 'shared')", name="ck_knowledge_document_audience"),
        sa.ForeignKeyConstraint(["created_by"], ["actors.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stable_key", name="uq_knowledge_document_stable_key"),
    )
    op.create_index("ix_knowledge_documents_audience_category", "knowledge_documents", ["audience", "category"])
    op.create_table(
        "knowledge_document_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("content_checksum", sa.String(length=64), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("published_by", sa.String(length=64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('draft', 'indexing', 'ready', 'published', 'retired')", name="ck_knowledge_document_version_status"),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"]),
        sa.ForeignKeyConstraint(["published_by"], ["actors.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "version", name="uq_knowledge_document_version"),
    )
    op.create_index("ix_knowledge_document_versions_document_id", "knowledge_document_versions", ["document_id"])
    op.create_index("ix_knowledge_versions_status", "knowledge_document_versions", ["status", "published_at"])
    op.create_index(
        "uq_knowledge_one_published_version", "knowledge_document_versions", ["document_id"], unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("chunk_checksum", sa.String(length=64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(512), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_version_id"], ["knowledge_document_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_version_id", "ordinal", name="uq_knowledge_chunk_ordinal"),
    )
    op.create_index("ix_knowledge_chunks_version", "knowledge_chunks", ["document_version_id"])
    op.execute("CREATE INDEX ix_knowledge_chunks_search ON knowledge_chunks USING gin (to_tsvector('simple', search_text))")
    op.execute("CREATE INDEX ix_knowledge_chunks_embedding_hnsw ON knowledge_chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)")
    op.create_table(
        "knowledge_ingestion_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_knowledge_ingestion_job_status"),
        sa.ForeignKeyConstraint(["document_version_id"], ["knowledge_document_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_version_id", name="uq_knowledge_ingestion_job_version"),
    )
    op.create_index("ix_knowledge_ingestion_jobs_claim", "knowledge_ingestion_jobs", ["status", "lease_until", "created_at"])
    op.create_table(
        "knowledge_retrieval_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("agent_run_id", sa.String(length=36), nullable=True),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("route", sa.String(length=32), nullable=False),
        sa.Column("query_digest", sa.String(length=64), nullable=False),
        sa.Column("audience", sa.String(length=16), nullable=False),
        sa.Column("candidate_chunk_ids", sa.JSON(), nullable=False),
        sa.Column("cited_chunk_ids", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["actors.id"]),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_retrieval_logs_agent_run_id", "knowledge_retrieval_logs", ["agent_run_id"])
    op.create_index("ix_knowledge_retrieval_logs_actor_id", "knowledge_retrieval_logs", ["actor_id"])
    op.create_index("ix_knowledge_retrieval_logs_actor_created", "knowledge_retrieval_logs", ["actor_id", "created_at"])
    op.create_table(
        "knowledge_feedback",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_log_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("rating", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["actors.id"]),
        sa.ForeignKeyConstraint(["retrieval_log_id"], ["knowledge_retrieval_logs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("retrieval_log_id", "actor_id", name="uq_knowledge_feedback_actor_retrieval"),
    )
    op.create_index("ix_knowledge_feedback_retrieval_log_id", "knowledge_feedback", ["retrieval_log_id"])
    op.execute("""
        CREATE FUNCTION prevent_published_knowledge_version_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND OLD.status IN ('published', 'retired') THEN
                RAISE EXCEPTION 'published knowledge versions cannot be deleted';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.status = 'retired' THEN
                RAISE EXCEPTION 'retired knowledge versions are immutable';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.status = 'published' THEN
                IF NEW.document_id IS DISTINCT FROM OLD.document_id
                   OR NEW.version IS DISTINCT FROM OLD.version
                   OR NEW.content_markdown IS DISTINCT FROM OLD.content_markdown
                   OR NEW.content_checksum IS DISTINCT FROM OLD.content_checksum
                   OR NEW.embedding_model IS DISTINCT FROM OLD.embedding_model
                   OR NEW.published_by IS DISTINCT FROM OLD.published_by
                   OR NEW.published_at IS DISTINCT FROM OLD.published_at
                   OR NEW.status <> 'retired' OR NEW.retired_at IS NULL THEN
                    RAISE EXCEPTION 'published knowledge content is immutable; it may only retire';
                END IF;
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER knowledge_document_versions_immutable
        BEFORE UPDATE OR DELETE ON knowledge_document_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_published_knowledge_version_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER knowledge_document_versions_immutable ON knowledge_document_versions")
    op.execute("DROP FUNCTION prevent_published_knowledge_version_mutation()")
    op.drop_table("knowledge_feedback")
    op.drop_table("knowledge_retrieval_logs")
    op.drop_table("knowledge_ingestion_jobs")
    op.execute("DROP INDEX ix_knowledge_chunks_embedding_hnsw")
    op.execute("DROP INDEX ix_knowledge_chunks_search")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_document_versions")
    op.drop_table("knowledge_documents")
