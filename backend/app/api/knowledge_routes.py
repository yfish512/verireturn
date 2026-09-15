"""M4 APIs for knowledge search and operations-managed document lifecycle."""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..auth import ActorContext, current_actor, require_ops_manager, require_operator
from ..database import get_db
from ..security import validate_idempotency_key
from ..domain.knowledge import (
    add_feedback,
    create_document,
    create_document_version,
    list_documents,
    publish_version,
    queue_ingestion,
    retrieve,
)
from ..domain.service import DomainError
from ..schemas import (
    KnowledgeDocumentCreateRequest,
    KnowledgeDocumentDetailResponse,
    KnowledgeDocumentResponse,
    KnowledgeDocumentVersionCreateRequest,
    KnowledgeFeedbackRequest,
    KnowledgeFeedbackResponse,
    KnowledgeIngestionJobResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeVersionResponse,
)


knowledge_router = APIRouter(prefix="/knowledge", tags=["knowledge"])
ops_knowledge_router = APIRouter(prefix="/ops/knowledge", tags=["knowledge-operations"])


def _error(error: DomainError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail={"code": error.code, "message": error.message})


def _idempotency_key(value: str = Header(alias="Idempotency-Key", min_length=8, max_length=128)) -> str:
    return validate_idempotency_key(value)


@knowledge_router.post("/search", response_model=KnowledgeSearchResponse)
def search_knowledge(
    request: KnowledgeSearchRequest,
    actor: ActorContext = Depends(current_actor),
    db: Session = Depends(get_db),
):
    try:
        return retrieve(db, actor.id, actor.role, request.query, limit=request.limit)
    except DomainError as error:
        raise _error(error) from error


@knowledge_router.post("/retrievals/{retrieval_id}/feedback", response_model=KnowledgeFeedbackResponse, status_code=201)
def submit_feedback(
    retrieval_id: str,
    request: KnowledgeFeedbackRequest,
    actor: ActorContext = Depends(current_actor),
    db: Session = Depends(get_db),
):
    try:
        return add_feedback(db, retrieval_id, actor.id, request.rating, request.reason)
    except DomainError as error:
        raise _error(error) from error


@ops_knowledge_router.get("/documents", response_model=list[KnowledgeDocumentDetailResponse])
def read_documents(_: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    return [
        KnowledgeDocumentDetailResponse(
            **KnowledgeDocumentResponse.model_validate(document).model_dump(),
            versions=[KnowledgeVersionResponse.model_validate(version) for version in versions],
        )
        for document, versions in list_documents(db)
    ]


@ops_knowledge_router.post("/documents", response_model=KnowledgeDocumentDetailResponse, status_code=201)
def create_knowledge_document(
    request: KnowledgeDocumentCreateRequest,
    actor: ActorContext = Depends(require_ops_manager),
    key: str = Depends(_idempotency_key),
    db: Session = Depends(get_db),
):
    try:
        document, version = create_document(db, actor.id, request, key)
        return KnowledgeDocumentDetailResponse(
            **KnowledgeDocumentResponse.model_validate(document).model_dump(),
            versions=[KnowledgeVersionResponse.model_validate(version)],
        )
    except DomainError as error:
        raise _error(error) from error


@ops_knowledge_router.post("/documents/{document_id}/versions", response_model=KnowledgeVersionResponse, status_code=201)
def create_knowledge_version(
    document_id: str,
    request: KnowledgeDocumentVersionCreateRequest,
    actor: ActorContext = Depends(require_ops_manager),
    key: str = Depends(_idempotency_key),
    db: Session = Depends(get_db),
):
    try:
        return create_document_version(db, actor.id, document_id, request.content_markdown, key)
    except DomainError as error:
        raise _error(error) from error


@ops_knowledge_router.post("/versions/{version_id}/ingestion", response_model=KnowledgeIngestionJobResponse, status_code=202)
def queue_knowledge_ingestion(
    version_id: str,
    actor: ActorContext = Depends(require_ops_manager),
    key: str = Depends(_idempotency_key),
    db: Session = Depends(get_db),
):
    try:
        return queue_ingestion(db, actor.id, version_id, key)
    except DomainError as error:
        raise _error(error) from error


@ops_knowledge_router.post("/versions/{version_id}/publish", response_model=KnowledgeVersionResponse)
def publish_knowledge_version(
    version_id: str,
    actor: ActorContext = Depends(require_ops_manager),
    key: str = Depends(_idempotency_key),
    db: Session = Depends(get_db),
):
    try:
        return publish_version(db, actor.id, version_id, key)
    except DomainError as error:
        raise _error(error) from error
