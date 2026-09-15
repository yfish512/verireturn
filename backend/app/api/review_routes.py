from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth import ActorContext, current_actor, require_operator, require_ops_manager
from ..database import get_db
from ..security import validate_idempotency_key
from ..domain.review_service import (
    claim_review_ticket,
    create_review_ticket,
    attach_review_evidence,
    create_review_upload,
    decide_review_ticket,
    get_owned_review_ticket,
    get_review_ticket,
    list_review_events,
    list_review_tickets,
    list_policy_versions,
    publish_policy_version,
    supplement_review_ticket,
)
from ..domain.service import DomainError
from ..domain.ops_metrics import review_metrics
from ..schemas import (
    ReviewClaimRequest,
    ReviewDecisionRequest,
    ReviewAttachmentRequest, ReviewAttachmentResponse, ReviewUploadRequest, ReviewUploadResponse,
    ReviewEventResponse,
    OpsMetricsResponse,
    PolicyVersionPublishRequest,
    PolicyVersionResponse,
    ReviewTicketCreateRequest,
    ReviewTicketResponse,
    ReviewTicketSupplementRequest,
)


customer_router = APIRouter(prefix="/review-tickets", tags=["review-tickets"])
ops_router = APIRouter(prefix="/ops", tags=["operations"])


def domain_http_error(error: DomainError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail={"code": error.code, "message": error.message})


def idempotency_key(value: str = Header(alias="Idempotency-Key", min_length=8, max_length=128)) -> str:
    return validate_idempotency_key(value)


def request_id(value: str | None = Header(default=None, alias="X-Request-Id", max_length=64)) -> str | None:
    return value


@customer_router.post("", response_model=ReviewTicketResponse, status_code=201)
def submit_review_ticket(
    request: ReviewTicketCreateRequest,
    actor: ActorContext = Depends(current_actor),
    key: str = Depends(idempotency_key),
    trace_id: str | None = Depends(request_id),
    db: Session = Depends(get_db),
):
    if actor.role != "customer":
        raise HTTPException(status_code=403, detail={"code": "CUSTOMER_ROLE_REQUIRED", "message": "只有客户可提交审核申请。"})
    try:
        return create_review_ticket(db, actor.id, request, key, trace_id)
    except DomainError as error:
        raise domain_http_error(error) from error


@customer_router.post("/uploads", response_model=ReviewUploadResponse, status_code=201)
def upload_review_evidence(request: ReviewUploadRequest, actor: ActorContext = Depends(current_actor), db: Session = Depends(get_db)):
    if actor.role != "customer":
        raise HTTPException(status_code=403, detail={"code": "CUSTOMER_ROLE_REQUIRED", "message": "只有客户可上传审核材料。"})
    try:
        return create_review_upload(db, actor.id, request.filename, request.media_type, request.content_base64)
    except DomainError as error:
        raise domain_http_error(error) from error

@customer_router.get("/{ticket_id}", response_model=ReviewTicketResponse)
def read_review_ticket(ticket_id: str, actor: ActorContext = Depends(current_actor), db: Session = Depends(get_db)):
    try:
        return get_owned_review_ticket(db, ticket_id, actor.id)
    except DomainError as error:
        raise domain_http_error(error) from error


@customer_router.get("/{ticket_id}/timeline", response_model=list[ReviewEventResponse])
def read_customer_timeline(ticket_id: str, actor: ActorContext = Depends(current_actor), db: Session = Depends(get_db)):
    try:
        get_owned_review_ticket(db, ticket_id, actor.id)
        return list_review_events(db, ticket_id)
    except DomainError as error:
        raise domain_http_error(error) from error


@customer_router.post("/{ticket_id}/supplements", response_model=ReviewTicketResponse)
def add_review_supplement(
    ticket_id: str,
    request: ReviewTicketSupplementRequest,
    actor: ActorContext = Depends(current_actor),
    key: str = Depends(idempotency_key),
    trace_id: str | None = Depends(request_id),
    db: Session = Depends(get_db),
):
    try:
        return supplement_review_ticket(db, ticket_id, actor.id, request.content, request.expected_version, key, trace_id)
    except DomainError as error:
        raise domain_http_error(error) from error


@ops_router.get("/review-tickets", response_model=list[ReviewTicketResponse])
def list_ops_review_tickets(
    status: str | None = Query(default=None, pattern="^(open|claimed|waiting_customer|approved|rejected|closed|expired)$"),
    _: ActorContext = Depends(require_operator),
    db: Session = Depends(get_db),
):
    return list_review_tickets(db, status)


@ops_router.get("/review-tickets/{ticket_id}", response_model=ReviewTicketResponse)
def read_ops_review_ticket(ticket_id: str, _: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    try:
        return get_review_ticket(db, ticket_id)
    except DomainError as error:
        raise domain_http_error(error) from error


@ops_router.get("/review-tickets/{ticket_id}/timeline", response_model=list[ReviewEventResponse])
def read_ops_timeline(ticket_id: str, _: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    try:
        return list_review_events(db, ticket_id)
    except DomainError as error:
        raise domain_http_error(error) from error


@ops_router.get("/metrics/summary", response_model=OpsMetricsResponse)
def read_ops_metrics(
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    _: ActorContext = Depends(require_operator),
    db: Session = Depends(get_db),
):
    if start_at and end_at and start_at >= end_at:
        raise HTTPException(status_code=422, detail={"code": "INVALID_TIME_RANGE", "message": "start_at 必须早于 end_at。"})
    return review_metrics(db, start_at, end_at)


@ops_router.get("/policy-versions", response_model=list[PolicyVersionResponse])
def read_policy_versions(_: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    return list_policy_versions(db)


@ops_router.post("/policy-versions", response_model=PolicyVersionResponse, status_code=201)
def publish_policy(
    request: PolicyVersionPublishRequest,
    actor: ActorContext = Depends(require_ops_manager),
    key: str = Depends(idempotency_key),
    trace_id: str | None = Depends(request_id),
    db: Session = Depends(get_db),
):
    try:
        return publish_policy_version(db, actor.id, request, key, trace_id)
    except DomainError as error:
        raise domain_http_error(error) from error


@ops_router.post("/review-tickets/{ticket_id}/claim", response_model=ReviewTicketResponse)
def claim_ops_review_ticket(
    ticket_id: str,
    request: ReviewClaimRequest,
    actor: ActorContext = Depends(require_operator),
    key: str = Depends(idempotency_key),
    trace_id: str | None = Depends(request_id),
    db: Session = Depends(get_db),
):
    try:
        return claim_review_ticket(db, ticket_id, actor.id, actor.role, request.expected_version, key, trace_id)
    except DomainError as error:
        raise domain_http_error(error) from error


@ops_router.post("/review-tickets/{ticket_id}/decisions", response_model=ReviewTicketResponse)
def decide_ops_review_ticket(
    ticket_id: str,
    request: ReviewDecisionRequest,
    actor: ActorContext = Depends(require_operator),
    key: str = Depends(idempotency_key),
    trace_id: str | None = Depends(request_id),
    db: Session = Depends(get_db),
):
    try:
        return decide_review_ticket(db, ticket_id, actor.id, actor.role, request, key, trace_id)
    except DomainError as error:
        raise domain_http_error(error) from error

@customer_router.post("/{ticket_id}/attachments", response_model=ReviewAttachmentResponse, status_code=201)
def attach_customer_evidence(ticket_id: str, request: ReviewAttachmentRequest, actor: ActorContext = Depends(current_actor), trace_id: str | None = Depends(request_id), db: Session = Depends(get_db)):
    try:
        return attach_review_evidence(db, ticket_id, actor.id, request.object_ref, request.content_hash, request.media_type, request.expected_version, trace_id)
    except DomainError as error:
        raise domain_http_error(error) from error
