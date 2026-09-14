"""M4 knowledge lifecycle, ingestion and permission-scoped hybrid retrieval.

The module is intentionally separate from the transactional policy service.
Knowledge can explain a policy to a person; it is never consulted when a
refund amount, eligibility, permission or state transition is decided.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Iterable, Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

import jieba
from fastembed import TextEmbedding
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeFeedback,
    KnowledgeIngestionJob,
    KnowledgeRetrievalLog,
)
from ..schemas import KnowledgeDocumentCreateRequest
from .service import DomainError, request_fingerprint


EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
EMBEDDING_DIMENSIONS = 512
RRF_K = 60


class EmbeddingProvider(Protocol):
    model_name: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedProvider:
    """Lazy local embedding adapter; model weights are never loaded at API boot."""

    model_name = EMBEDDING_MODEL
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(self) -> None:
        # Keep model artifacts out of the repository and permit an operations
        # deployment to mount a read-only model cache at a different path.
        self._model = TextEmbedding(
            model_name=self.model_name, cache_dir=os.getenv("KNOWLEDGE_EMBEDDING_CACHE_DIR", ".local/models/fastembed"),
            # The bootstrap script fetches the pinned artifact from FastEmbed's
            # official mirror. Runtime deliberately uses local-only mode so a
            # production worker cannot silently change/download a model.
            local_files_only=True,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = [list(map(float, vector)) for vector in self._model.embed(texts)]
        if any(len(vector) != self.dimensions for vector in vectors):
            raise RuntimeError("EMBEDDING_DIMENSION_MISMATCH")
        return vectors


@lru_cache(maxsize=1)
def get_embedding_provider() -> FastEmbedProvider:
    return FastEmbedProvider()


@dataclass(frozen=True)
class RetrievalHit:
    chunk_id: str
    document_id: str
    document_version_id: str
    title: str
    category: str
    version: int
    content: str
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    retrieval_id: str
    hits: list[RetrievalHit]


def content_checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def segment_for_search(text: str) -> str:
    """Give PostgreSQL's `simple` dictionary meaningful Chinese tokens."""
    return " ".join(token.strip() for token in jieba.lcut(text) if token.strip())


def split_markdown(content: str, max_chars: int = 560) -> list[str]:
    """Keep headings/paragraphs together where possible and split long text safely."""
    normalized = re.sub(r"\r\n?", "\n", content).strip()
    if not normalized:
        raise DomainError("KNOWLEDGE_CONTENT_EMPTY", "知识文档内容不能为空。")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidates = [paragraph]
        if len(paragraph) > max_chars:
            candidates = [piece.strip() for piece in re.split(r"(?<=[。！？；])", paragraph) if piece.strip()]
        for piece in candidates:
            if current and len(current) + len(piece) + 2 > max_chars:
                chunks.append(current)
                current = ""
            if len(piece) > max_chars:
                for offset in range(0, len(piece), max_chars):
                    partial = piece[offset:offset + max_chars]
                    if len(partial) == max_chars:
                        chunks.append(partial)
                    else:
                        current = partial
            else:
                current = f"{current}\n\n{piece}".strip()
    if current:
        chunks.append(current)
    return chunks


def _idempotent_version(
    db: Session, actor_id: str, operation: str, idempotency_key: str, payload_hash: str
) -> KnowledgeDocumentVersion | None:
    from ..models import IdempotencyRecord

    record = db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.actor_id == actor_id,
        IdempotencyRecord.operation == operation,
        IdempotencyRecord.idempotency_key == idempotency_key,
    ))
    if record is None:
        return None
    if record.request_hash != payload_hash:
        raise DomainError("IDEMPOTENCY_KEY_CONFLICT", "同一幂等键不能用于不同请求。", 409)
    version = db.get(KnowledgeDocumentVersion, record.resource_id)
    if version is None:
        raise DomainError("IDEMPOTENCY_RECORD_INVALID", "幂等记录关联的知识版本不存在。", 500)
    return version


def _record_idempotency(
    db: Session, actor_id: str, operation: str, idempotency_key: str, payload_hash: str, version_id: str
) -> None:
    from ..models import IdempotencyRecord

    db.add(IdempotencyRecord(
        actor_id=actor_id, operation=operation, idempotency_key=idempotency_key,
        request_hash=payload_hash, resource_type="knowledge_document_version", resource_id=version_id,
    ))


def _commit_version(
    db: Session, actor_id: str, operation: str, key: str, payload_hash: str, version_id: str
) -> KnowledgeDocumentVersion:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        replay = _idempotent_version(db, actor_id, operation, key, payload_hash)
        if replay is None:
            raise DomainError("TRANSACTION_CONFLICT", "事务冲突，请使用相同幂等键重试。", 409) from error
        return replay
    version = db.get(KnowledgeDocumentVersion, version_id)
    if version is None:
        raise DomainError("KNOWLEDGE_VERSION_NOT_FOUND", "知识版本不存在。", 404)
    return version


def create_document(
    db: Session, actor_id: str, request: KnowledgeDocumentCreateRequest, idempotency_key: str
) -> tuple[KnowledgeDocument, KnowledgeDocumentVersion]:
    operation = "create_knowledge_document"
    payload = request.model_dump()
    payload_hash = request_fingerprint(payload)
    replay = _idempotent_version(db, actor_id, operation, idempotency_key, payload_hash)
    if replay is not None:
        document = db.get(KnowledgeDocument, replay.document_id)
        if document is None:
            raise DomainError("IDEMPOTENCY_RECORD_INVALID", "幂等记录关联的知识文档不存在。", 500)
        return document, replay
    if db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.stable_key == request.stable_key)) is not None:
        raise DomainError("KNOWLEDGE_STABLE_KEY_EXISTS", "知识文档标识已存在；请为更新创建新版本。", 409)
    now = datetime.now(timezone.utc)
    document = KnowledgeDocument(
        id=str(uuid4()), stable_key=request.stable_key, title=request.title, audience=request.audience,
        category=request.category, created_by=actor_id, created_at=now, updated_at=now,
    )
    version = KnowledgeDocumentVersion(
        id=str(uuid4()), document_id=document.id, version=1, status="draft", content_markdown=request.content_markdown.strip(),
        content_checksum=content_checksum(request.content_markdown.strip()), embedding_model=EMBEDDING_MODEL, created_at=now,
    )
    db.add_all([document, version])
    _record_idempotency(db, actor_id, operation, idempotency_key, payload_hash, version.id)
    result = _commit_version(db, actor_id, operation, idempotency_key, payload_hash, version.id)
    return document, result


def create_document_version(
    db: Session, actor_id: str, document_id: str, content_markdown: str, idempotency_key: str
) -> KnowledgeDocumentVersion:
    operation = "create_knowledge_document_version"
    content = content_markdown.strip()
    if not content:
        raise DomainError("KNOWLEDGE_CONTENT_EMPTY", "知识文档内容不能为空。")
    payload_hash = request_fingerprint({"document_id": document_id, "content_checksum": content_checksum(content)})
    replay = _idempotent_version(db, actor_id, operation, idempotency_key, payload_hash)
    if replay is not None:
        return replay
    document = db.get(KnowledgeDocument, document_id)
    if document is None:
        raise DomainError("KNOWLEDGE_DOCUMENT_NOT_FOUND", "知识文档不存在。", 404)
    latest = db.scalar(select(func.max(KnowledgeDocumentVersion.version)).where(KnowledgeDocumentVersion.document_id == document_id)) or 0
    version = KnowledgeDocumentVersion(
        id=str(uuid4()), document_id=document_id, version=latest + 1, status="draft", content_markdown=content,
        content_checksum=content_checksum(content), embedding_model=EMBEDDING_MODEL,
    )
    db.add(version)
    _record_idempotency(db, actor_id, operation, idempotency_key, payload_hash, version.id)
    return _commit_version(db, actor_id, operation, idempotency_key, payload_hash, version.id)


def queue_ingestion(db: Session, actor_id: str, version_id: str, idempotency_key: str) -> KnowledgeIngestionJob:
    operation = "queue_knowledge_ingestion"
    payload_hash = request_fingerprint({"version_id": version_id})
    replay = _idempotent_version(db, actor_id, operation, idempotency_key, payload_hash)
    if replay is not None:
        job = db.scalar(select(KnowledgeIngestionJob).where(KnowledgeIngestionJob.document_version_id == replay.id))
        if job is None:
            raise DomainError("IDEMPOTENCY_RECORD_INVALID", "幂等记录关联的索引任务不存在。", 500)
        return job
    version = db.scalar(select(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.id == version_id).with_for_update())
    if version is None:
        raise DomainError("KNOWLEDGE_VERSION_NOT_FOUND", "知识版本不存在。", 404)
    existing = db.scalar(select(KnowledgeIngestionJob).where(KnowledgeIngestionJob.document_version_id == version_id))
    if existing is not None:
        if existing.status == "succeeded":
            _record_idempotency(db, actor_id, operation, idempotency_key, payload_hash, version.id)
            db.commit()
            return existing
        if existing.status == "failed":
            # Retain the same task identity and attempt history so an operator
            # can recover a transient embedding failure without duplicate jobs.
            existing.status = "queued"
            existing.lease_until = None
            existing.last_error = None
            version.status = "indexing"
            _record_idempotency(db, actor_id, operation, idempotency_key, payload_hash, version.id)
            _commit_version(db, actor_id, operation, idempotency_key, payload_hash, version.id)
            db.refresh(existing)
            return existing
        raise DomainError("KNOWLEDGE_INGESTION_ALREADY_EXISTS", "该版本已有索引任务。", 409)
    if version.status not in {"draft", "ready"}:
        raise DomainError("KNOWLEDGE_VERSION_NOT_INDEXABLE", f"当前版本为 {version.status}，不能建立索引。", 409)
    version.status = "indexing"
    job = KnowledgeIngestionJob(id=str(uuid4()), document_version_id=version.id, status="queued", attempts=0)
    db.add(job)
    _record_idempotency(db, actor_id, operation, idempotency_key, payload_hash, version.id)
    _commit_version(db, actor_id, operation, idempotency_key, payload_hash, version.id)
    db.refresh(job)
    return job


def publish_version(db: Session, actor_id: str, version_id: str, idempotency_key: str) -> KnowledgeDocumentVersion:
    operation = "publish_knowledge_document_version"
    payload_hash = request_fingerprint({"version_id": version_id})
    replay = _idempotent_version(db, actor_id, operation, idempotency_key, payload_hash)
    if replay is not None:
        return replay
    version = db.scalar(select(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.id == version_id).with_for_update())
    if version is None:
        raise DomainError("KNOWLEDGE_VERSION_NOT_FOUND", "知识版本不存在。", 404)
    if version.status != "ready":
        raise DomainError("KNOWLEDGE_VERSION_NOT_READY", "只有索引完成的知识版本可以发布。", 409)
    # Version rows differ for concurrent publishers. Lock the stable parent
    # document as well, otherwise a waiter can observe the old published row
    # after retirement and race to insert a second published version.
    document = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == version.document_id).with_for_update())
    if document is None:
        raise DomainError("KNOWLEDGE_DOCUMENT_NOT_FOUND", "知识文档不存在。", 404)
    old = db.scalar(select(KnowledgeDocumentVersion).where(
        KnowledgeDocumentVersion.document_id == version.document_id,
        KnowledgeDocumentVersion.status == "published",
    ).with_for_update())
    now = datetime.now(timezone.utc)
    if old is not None:
        old.status = "retired"
        old.retired_at = now
        # A partial unique index permits only one published version. Flush the
        # retirement before marking the replacement published; SQLAlchemy's
        # unit-of-work order alone does not guarantee this update order.
        db.flush()
    version.status = "published"
    version.published_by = actor_id
    version.published_at = now
    _record_idempotency(db, actor_id, operation, idempotency_key, payload_hash, version.id)
    return _commit_version(db, actor_id, operation, idempotency_key, payload_hash, version.id)


def _claim_job(db: Session, lease_seconds: int = 180) -> KnowledgeIngestionJob | None:
    now = datetime.now(timezone.utc)
    job = db.scalar(select(KnowledgeIngestionJob).where(or_(
        KnowledgeIngestionJob.status == "queued",
        (KnowledgeIngestionJob.status == "running") & (KnowledgeIngestionJob.lease_until < now),
    )).order_by(KnowledgeIngestionJob.created_at).with_for_update(skip_locked=True))
    if job is None:
        return None
    job.status = "running"
    job.attempts += 1
    job.lease_until = now + timedelta(seconds=lease_seconds)
    job.last_error = None
    db.commit()
    db.refresh(job)
    return job


def process_one_ingestion_job(db: Session, provider: EmbeddingProvider | None = None) -> str | None:
    """Claim and index one job. The lease makes a crashed worker recoverable."""
    job = _claim_job(db)
    if job is None:
        return None
    provider = provider or get_embedding_provider()
    try:
        version = db.get(KnowledgeDocumentVersion, job.document_version_id)
        if version is None or version.status != "indexing":
            raise RuntimeError("KNOWLEDGE_JOB_VERSION_INVALID")
        chunks = split_markdown(version.content_markdown)
        embeddings = provider.embed(chunks)
        if len(embeddings) != len(chunks):
            raise RuntimeError("EMBEDDING_RESULT_COUNT_MISMATCH")
        for ordinal, (chunk_text, embedding) in enumerate(zip(chunks, embeddings), start=1):
            checksum = content_checksum(chunk_text)
            chunk_id = str(uuid5(NAMESPACE_URL, f"verireturn:{version.id}:{ordinal}:{checksum}"))
            existing = db.get(KnowledgeChunk, chunk_id)
            if existing is None:
                db.add(KnowledgeChunk(
                    id=chunk_id, document_version_id=version.id, ordinal=ordinal, content=chunk_text,
                    search_text=segment_for_search(chunk_text), chunk_checksum=checksum,
                    token_count=len(jieba.lcut(chunk_text)), embedding=embedding, embedding_model=provider.model_name,
                ))
        job.status = "succeeded"
        job.lease_until = None
        version.status = "ready"
        db.commit()
        return job.id
    except Exception as error:
        db.rollback()
        failed_job = db.get(KnowledgeIngestionJob, job.id)
        if failed_job is not None:
            failed_job.status = "failed"
            failed_job.lease_until = None
            failed_job.last_error = str(error)[:2000]
            db.commit()
        raise


def _allowed_audiences(actor_role: str) -> list[str]:
    if actor_role == "customer":
        return ["customer", "shared"]
    if actor_role in {"operator", "ops_manager"}:
        return ["customer", "operator", "shared"]
    return []


def _rows_for_visible_chunks(db: Session, audiences: list[str]):
    return list(db.execute(select(KnowledgeChunk, KnowledgeDocumentVersion, KnowledgeDocument).join(
        KnowledgeDocumentVersion, KnowledgeChunk.document_version_id == KnowledgeDocumentVersion.id
    ).join(KnowledgeDocument, KnowledgeDocumentVersion.document_id == KnowledgeDocument.id).where(
        KnowledgeDocumentVersion.status == "published", KnowledgeDocument.audience.in_(audiences),
        KnowledgeChunk.embedding.is_not(None),
    )))


def _postgres_rankings(
    db: Session, audiences: list[str], query_tokens: str, query_embedding: list[float], candidate_limit: int
) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """Execute both retrieval legs inside PostgreSQL, where their indexes live."""
    visibility = (
        KnowledgeDocumentVersion.status == "published",
        KnowledgeDocument.audience.in_(audiences),
        KnowledgeChunk.embedding.is_not(None),
    )
    text_vector = func.to_tsvector("simple", KnowledgeChunk.search_text)
    text_query = func.plainto_tsquery("simple", query_tokens)
    lexical_rank = func.ts_rank_cd(text_vector, text_query).label("lexical_rank")
    lexical_rows = list(db.execute(select(KnowledgeChunk.id, lexical_rank).join(
        KnowledgeDocumentVersion, KnowledgeChunk.document_version_id == KnowledgeDocumentVersion.id
    ).join(KnowledgeDocument, KnowledgeDocumentVersion.document_id == KnowledgeDocument.id).where(
        *visibility, text_vector.op("@@")(text_query)
    ).order_by(lexical_rank.desc(), KnowledgeChunk.id).limit(candidate_limit)))
    distance = KnowledgeChunk.embedding.cosine_distance(query_embedding).label("distance")
    semantic_rows = list(db.execute(select(KnowledgeChunk.id, distance).join(
        KnowledgeDocumentVersion, KnowledgeChunk.document_version_id == KnowledgeDocumentVersion.id
    ).join(KnowledgeDocument, KnowledgeDocumentVersion.document_id == KnowledgeDocument.id).where(
        *visibility
    ).order_by(distance, KnowledgeChunk.id).limit(candidate_limit)))
    return (
        [(chunk_id, float(score)) for chunk_id, score in lexical_rows],
        [(chunk_id, 1.0 - float(distance_value)) for chunk_id, distance_value in semantic_rows],
    )


def _sqlite_rank(query_tokens: set[str], text: str) -> float:
    tokens = set(text.split())
    return len(query_tokens & tokens) / max(len(query_tokens), 1)


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def retrieve(
    db: Session, actor_id: str, actor_role: str, query: str, *, limit: int = 4,
    provider: EmbeddingProvider | None = None, agent_run_id: str | None = None, route: str = "knowledge_qa",
) -> RetrievalResult:
    started = time.perf_counter()
    query = query.strip()
    if not query:
        raise DomainError("KNOWLEDGE_QUERY_EMPTY", "检索问题不能为空。")
    audiences = _allowed_audiences(actor_role)
    if not audiences:
        raise DomainError("KNOWLEDGE_ACCESS_DENIED", "当前身份不能检索知识库。", 403)
    provider = provider or get_embedding_provider()
    query_embedding = provider.embed([query])[0]
    query_tokens = segment_for_search(query)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        lexical, semantic = _postgres_rankings(db, audiences, query_tokens, query_embedding, max(limit * 8, 24))
        candidate_ids = list(dict.fromkeys([chunk_id for chunk_id, _ in lexical + semantic]))
        rows = [] if not candidate_ids else list(db.execute(select(KnowledgeChunk, KnowledgeDocumentVersion, KnowledgeDocument).join(
            KnowledgeDocumentVersion, KnowledgeChunk.document_version_id == KnowledgeDocumentVersion.id
        ).join(KnowledgeDocument, KnowledgeDocumentVersion.document_id == KnowledgeDocument.id).where(KnowledgeChunk.id.in_(candidate_ids))))
    else:
        rows = _rows_for_visible_chunks(db, audiences)
        lexical = sorted(
            ((chunk.id, _sqlite_rank(set(query_tokens.split()), chunk.search_text)) for chunk, _, _ in rows),
            key=lambda item: (-item[1], item[0]),
        )
        semantic = sorted(
            ((chunk.id, _cosine(query_embedding, list(chunk.embedding))) for chunk, _, _ in rows if chunk.embedding is not None),
            key=lambda item: (-item[1], item[0]),
        )
    # RRF makes full-text ranks and vector distances comparable and keeps the
    # choice stable when a policy code has a strong lexical match.
    fused: dict[str, float] = {}
    for ranking in (lexical, semantic):
        for rank, (chunk_id, score) in enumerate(ranking, start=1):
            if score > 0:
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    rows_by_id = {chunk.id: (chunk, version, document) for chunk, version, document in rows}
    selected_ids = [chunk_id for chunk_id, _ in sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:limit]]
    hits = [
        RetrievalHit(
            chunk_id=chunk_id, document_id=document.id, document_version_id=version.id, title=document.title,
            category=document.category, version=version.version, content=chunk.content, score=round(fused[chunk_id], 6),
        )
        for chunk_id in selected_ids
        for chunk, version, document in [rows_by_id[chunk_id]]
    ]
    retrieval_id = str(uuid4())
    db.add(KnowledgeRetrievalLog(
        id=retrieval_id, agent_run_id=agent_run_id, actor_id=actor_id, route=route,
        query_digest=content_checksum(query), audience="|".join(audiences),
        candidate_chunk_ids=[hit.chunk_id for hit in hits], cited_chunk_ids=[],
        latency_ms=int((time.perf_counter() - started) * 1000),
    ))
    db.commit()
    return RetrievalResult(retrieval_id=retrieval_id, hits=hits)


def record_citations(db: Session, retrieval_id: str, actor_id: str, cited_chunk_ids: Iterable[str]) -> None:
    log = db.scalar(select(KnowledgeRetrievalLog).where(
        KnowledgeRetrievalLog.id == retrieval_id, KnowledgeRetrievalLog.actor_id == actor_id,
    ).with_for_update())
    if log is None:
        raise DomainError("KNOWLEDGE_RETRIEVAL_NOT_FOUND", "检索记录不存在或不属于当前用户。", 404)
    citations = list(dict.fromkeys(cited_chunk_ids))
    if not citations or not set(citations).issubset(set(log.candidate_chunk_ids)):
        raise DomainError("KNOWLEDGE_CITATION_INVALID", "回答引用不在本次可见检索结果中。", 422)
    log.cited_chunk_ids = citations
    db.commit()


def add_feedback(db: Session, retrieval_id: str, actor_id: str, rating: str, reason: str | None) -> KnowledgeFeedback:
    if rating not in {"helpful", "unhelpful"}:
        raise DomainError("KNOWLEDGE_FEEDBACK_INVALID", "反馈只能是 helpful 或 unhelpful。")
    log = db.scalar(select(KnowledgeRetrievalLog).where(
        KnowledgeRetrievalLog.id == retrieval_id, KnowledgeRetrievalLog.actor_id == actor_id,
    ))
    if log is None:
        raise DomainError("KNOWLEDGE_RETRIEVAL_NOT_FOUND", "检索记录不存在或不属于当前用户。", 404)
    existing = db.scalar(select(KnowledgeFeedback).where(
        KnowledgeFeedback.retrieval_log_id == retrieval_id, KnowledgeFeedback.actor_id == actor_id,
    ))
    if existing is not None:
        raise DomainError("KNOWLEDGE_FEEDBACK_EXISTS", "本次回答已经提交过反馈。", 409)
    feedback = KnowledgeFeedback(id=str(uuid4()), retrieval_log_id=retrieval_id, actor_id=actor_id, rating=rating, reason=reason)
    db.add(feedback)
    db.commit()
    return feedback


def list_documents(db: Session) -> list[tuple[KnowledgeDocument, list[KnowledgeDocumentVersion]]]:
    documents = list(db.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.category, KnowledgeDocument.stable_key)))
    return [(document, list(db.scalars(select(KnowledgeDocumentVersion).where(
        KnowledgeDocumentVersion.document_id == document.id
    ).order_by(KnowledgeDocumentVersion.version.desc())))) for document in documents]
