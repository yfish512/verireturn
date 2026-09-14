from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..auth import ActorContext, current_demo_user, require_operator, verify_fulfillment_webhook
from ..database import get_db
from ..domain.fulfillment import (
    acknowledge_incident, list_case_fulfillment, list_incidents, receive_provider_event,
    replay_deferred_event, resolve_incident,
)
from ..domain.service import DomainError, get_owned_case
from ..schemas import (
    FulfillmentIncidentActionRequest, FulfillmentIncidentResolveRequest, FulfillmentIncidentResponse,
    FulfillmentReplayRequest, FulfillmentStatusResponse, InboxEventResponse, ProviderWebhookRequest,
)

fulfillment_router = APIRouter(tags=["fulfillment"])


def domain_http_error(error: DomainError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail={"code": error.code, "message": error.message})


@fulfillment_router.get("/tools/after-sales/cases/{case_id}/fulfillment", response_model=FulfillmentStatusResponse)
def read_fulfillment_status(case_id: int, user_id: str = Depends(current_demo_user), db: Session = Depends(get_db)):
    try:
        case = get_owned_case(db, user_id, case_id)
        events, notifications = list_case_fulfillment(db, case.id)
        return {"case_id": case.id, "status": case.status, "events": events, "notifications": notifications}
    except DomainError as error:
        raise domain_http_error(error) from error


@fulfillment_router.post("/internal/fulfillment/webhooks/{provider}", response_model=InboxEventResponse)
async def provider_webhook(
    provider: str,
    request: Request,
    signature: str = Header(alias="X-Provider-Signature", min_length=8, max_length=200),
    timestamp: str = Header(alias="X-Provider-Timestamp", min_length=10, max_length=64),
    header_event_id: str = Header(alias="X-Provider-Event-Id", min_length=8, max_length=128),
    db: Session = Depends(get_db),
):
    if provider != "demo_fulfillment":
        raise HTTPException(status_code=404, detail={"code": "FULFILLMENT_PROVIDER_NOT_FOUND", "message": "未知履约 Provider。"})
    raw = await request.body()
    verify_fulfillment_webhook(raw, signature, timestamp)
    try:
        payload = ProviderWebhookRequest.model_validate_json(raw)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail={"code": "FULFILLMENT_EVENT_INVALID", "message": "履约回调内容无效。"}) from error
    if payload.event_id != header_event_id:
        raise HTTPException(status_code=422, detail={"code": "FULFILLMENT_EVENT_ID_MISMATCH", "message": "回调头与正文的事件 ID 不一致。"})
    try:
        return receive_provider_event(db, provider, payload.model_dump())
    except DomainError as error:
        raise domain_http_error(error) from error


@fulfillment_router.get("/ops/fulfillment/incidents", response_model=list[FulfillmentIncidentResponse])
def read_fulfillment_incidents(
    status: str | None = None, _: ActorContext = Depends(require_operator), db: Session = Depends(get_db),
):
    if status and status not in {"open", "acknowledged", "resolved"}:
        raise HTTPException(status_code=422, detail={"code": "FULFILLMENT_INCIDENT_STATUS_INVALID", "message": "事故状态无效。"})
    return list_incidents(db, status)


@fulfillment_router.post("/ops/fulfillment/incidents/{incident_id}/acknowledge", response_model=FulfillmentIncidentResponse)
def acknowledge_fulfillment_incident(
    incident_id: str, body: FulfillmentIncidentActionRequest, actor: ActorContext = Depends(require_operator), db: Session = Depends(get_db),
):
    try:
        return acknowledge_incident(db, incident_id, actor.id, body.expected_version)
    except DomainError as error:
        raise domain_http_error(error) from error


@fulfillment_router.post("/ops/fulfillment/incidents/{incident_id}/resolve", response_model=FulfillmentIncidentResponse)
def resolve_fulfillment_incident(
    incident_id: str, body: FulfillmentIncidentResolveRequest, actor: ActorContext = Depends(require_operator), db: Session = Depends(get_db),
):
    try:
        return resolve_incident(db, incident_id, actor.id, body.expected_version, body.resolution_note)
    except DomainError as error:
        raise domain_http_error(error) from error


@fulfillment_router.post("/ops/fulfillment/inbox-events/{inbox_event_id}/replay", response_model=InboxEventResponse)
def replay_fulfillment_event(
    inbox_event_id: str, _: FulfillmentReplayRequest, actor: ActorContext = Depends(require_operator), db: Session = Depends(get_db),
):
    try:
        return replay_deferred_event(db, inbox_event_id, actor.id)
    except DomainError as error:
        raise domain_http_error(error) from error
