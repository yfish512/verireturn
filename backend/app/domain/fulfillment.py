"""M5 reliable fulfillment primitives.

The module deliberately has no HTTP client dependency.  Provider and notification
adapters are protocols, so a production caller can use HTTP while tests run the
same retry/idempotency and database code against deterministic simulators.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AfterSalesCase, CustomerNotification, FulfillmentEvent, FulfillmentIncident,
    InboxEvent, OutboxDelivery, OutboxEvent,
)
from .service import DomainError, _audit

PICKUP_SCHEDULED = "pickup_scheduled"
PICKED_UP = "picked_up"
RETURN_RECEIVED = "return_received"
REFUND_PROCESSING = "refund_processing"
REPLACEMENT_SHIPPED = "replacement_shipped"
COMPLETED = "completed"

PROVIDER_EVENTS = {
    "pickup.collected", "return.received", "refund.processing", "refund.completed",
    "replacement.shipped", "replacement.delivered",
}


class FulfillmentProvider(Protocol):
    def request_pickup(self, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]: ...


class NotificationGateway(Protocol):
    def send(self, notification: CustomerNotification, idempotency_key: str) -> dict[str, Any]: ...


class SimulatorFulfillmentProvider:
    """Deterministic demo adapter; it only acknowledges a request, never fabricates callbacks."""
    def request_pickup(self, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        return {"accepted": True, "provider_request_id": f"sim-{idempotency_key[-16:]}", "case_id": payload["case_id"]}


class InAppNotificationGateway:
    def send(self, notification: CustomerNotification, idempotency_key: str) -> dict[str, Any]:
        return {"accepted": True, "notification_id": notification.id, "channel": notification.channel}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def payload_digest(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _new_id() -> str:
    return str(uuid.uuid4())


def enqueue_outbox(
    db: Session, *, aggregate_type: str, aggregate_id: str, event_type: str, destination: str,
    payload: dict[str, Any], idempotency_key: str,
) -> OutboxEvent:
    existing = db.scalar(select(OutboxEvent).where(
        OutboxEvent.aggregate_type == aggregate_type, OutboxEvent.aggregate_id == aggregate_id,
        OutboxEvent.event_type == event_type, OutboxEvent.idempotency_key == idempotency_key,
    ))
    if existing is not None:
        # A caller must never use one semantic key to silently alter an intent.
        if existing.destination != destination or existing.payload != payload:
            raise DomainError("OUTBOX_IDEMPOTENCY_CONFLICT", "同一履约幂等键不能改变投递内容。", 409)
        return existing
    event = OutboxEvent(
        id=_new_id(), aggregate_type=aggregate_type, aggregate_id=aggregate_id, event_type=event_type,
        destination=destination, payload=payload, idempotency_key=idempotency_key,
    )
    db.add(event)
    return event


def enqueue_pickup_request(db: Session, case: AfterSalesCase, command_key: str) -> OutboxEvent:
    return enqueue_outbox(
        db, aggregate_type="after_sales_case", aggregate_id=str(case.id), event_type="pickup.requested",
        destination="fulfillment_provider",
        payload={"case_id": case.id, "order_id": case.order_id, "request_type": case.request_type, "pickup_slot": case.pickup_slot},
        idempotency_key=f"pickup:{case.id}:{command_key}",
    )


def _queue_notification(db: Session, case: AfterSalesCase, template: str, content: str, dedupe_key: str) -> CustomerNotification:
    notification = db.scalar(select(CustomerNotification).where(
        CustomerNotification.case_id == case.id, CustomerNotification.dedupe_key == dedupe_key,
    ))
    if notification is not None:
        return notification
    notification = CustomerNotification(
        id=_new_id(), case_id=case.id, user_id=case.user_id, template=template, content=content, dedupe_key=dedupe_key,
    )
    db.add(notification)
    db.flush()
    enqueue_outbox(
        db, aggregate_type="customer_notification", aggregate_id=notification.id, event_type="notification.requested",
        destination="notification_gateway", payload={"notification_id": notification.id, "case_id": case.id, "template": template},
        idempotency_key=f"notification:{notification.id}",
    )
    return notification


def _create_incident(db: Session, case_id: int, incident_type: str, detail: str, dedupe_key: str, inbox_event_id: str | None = None) -> FulfillmentIncident:
    existing = db.scalar(select(FulfillmentIncident).where(
        FulfillmentIncident.case_id == case_id, FulfillmentIncident.dedupe_key == dedupe_key,
    ))
    if existing is not None:
        return existing
    incident = FulfillmentIncident(
        id=_new_id(), case_id=case_id, inbox_event_id=inbox_event_id, incident_type=incident_type,
        detail=detail, dedupe_key=dedupe_key,
    )
    db.add(incident)
    return incident


def _next_target(case: AfterSalesCase, event_type: str) -> tuple[str, str, str] | None:
    """Return target state, audit type and customer copy for one valid callback."""
    transitions = {
        (PICKUP_SCHEDULED, "pickup.collected"): (PICKED_UP, "FULFILLMENT_PICKED_UP", "快递员已取走您的退货包裹。"),
        (PICKED_UP, "return.received"): (RETURN_RECEIVED, "FULFILLMENT_RETURN_RECEIVED", "仓库已签收退货，正在处理。"),
        (RETURN_RECEIVED, "refund.processing"): (REFUND_PROCESSING, "FULFILLMENT_REFUND_PROCESSING", "退款正在处理，请留意到账通知。"),
        (REFUND_PROCESSING, "refund.completed"): (COMPLETED, "FULFILLMENT_REFUND_COMPLETED", "退款已完成。"),
        (RETURN_RECEIVED, "replacement.shipped"): (REPLACEMENT_SHIPPED, "FULFILLMENT_REPLACEMENT_SHIPPED", "换货商品已发出。"),
        (REPLACEMENT_SHIPPED, "replacement.delivered"): (COMPLETED, "FULFILLMENT_REPLACEMENT_DELIVERED", "换货商品已送达，售后流程完成。"),
    }
    result = transitions.get((case.status, event_type))
    if result is None:
        return None
    if event_type.startswith("refund.") and case.request_type != "refund":
        return None
    if event_type.startswith("replacement.") and case.request_type != "exchange":
        return None
    return result


def _apply_inbox_locked(db: Session, inbox: InboxEvent, case: AfterSalesCase) -> str:
    """Apply a row already protected by row locks; returns applied/deferred/rejected."""
    last_sequence = db.scalar(select(func.max(FulfillmentEvent.sequence_no)).where(FulfillmentEvent.case_id == case.id)) or 0
    if inbox.sequence_no <= last_sequence:
        inbox.status, inbox.rejection_code = "rejected", "SEQUENCE_REPLAY"
        _create_incident(db, case.id, "SEQUENCE_REPLAY", "收到已经处理过的履约序号。", f"sequence-replay:{inbox.provider}:{case.id}:{inbox.sequence_no}", inbox.id)
        return inbox.status
    if inbox.sequence_no > last_sequence + 1:
        inbox.status, inbox.rejection_code = "deferred", None
        _create_incident(db, case.id, "OUT_OF_ORDER_EVENT", "回调序号缺少前序事件，已等待重放。", f"out-of-order:{inbox.provider}:{case.id}:{inbox.sequence_no}", inbox.id)
        return inbox.status
    target = _next_target(case, inbox.event_type)
    if target is None:
        inbox.status, inbox.rejection_code = "rejected", "INVALID_FULFILLMENT_TRANSITION"
        _create_incident(
            db, case.id, "INVALID_FULFILLMENT_TRANSITION",
            f"状态 {case.status} 不能应用事件 {inbox.event_type}。",
            f"invalid-transition:{inbox.provider}:{inbox.provider_event_id}", inbox.id,
        )
        return inbox.status
    target_status, audit_type, copy = target
    case.status = target_status
    if inbox.event_type == "return.received" and case.request_type == "refund":
        from .payments import ensure_refund_intent
        ensure_refund_intent(db, case)
    if target_status == COMPLETED:
        case.completed_at = utcnow()
    inbox.status, inbox.rejection_code, inbox.applied_at = "applied", None, utcnow()
    db.add(FulfillmentEvent(
        id=_new_id(), case_id=case.id, inbox_event_id=inbox.id, provider=inbox.provider,
        provider_event_id=inbox.provider_event_id, event_type=inbox.event_type, sequence_no=inbox.sequence_no,
        payload=inbox.payload, occurred_at=inbox.occurred_at,
    ))
    _audit(db, case.id, audit_type, f"Provider {inbox.provider} 回调：{inbox.event_type}", "internal_service", inbox.provider, inbox.provider_event_id)
    _queue_notification(db, case, audit_type.lower(), copy, f"fulfillment:{inbox.provider}:{inbox.provider_event_id}")
    return inbox.status


def receive_provider_event(db: Session, provider: str, event: dict[str, Any]) -> InboxEvent:
    """Persist a verified provider event and apply it exactly once in local business terms."""
    event_id = str(event.get("event_id", ""))
    case_id = event.get("case_id")
    event_type = event.get("event_type")
    sequence_no = event.get("sequence_no")
    occurred_at = event.get("occurred_at")
    if not event_id or not isinstance(case_id, int) or event_type not in PROVIDER_EVENTS or not isinstance(sequence_no, int) or sequence_no < 1:
        raise DomainError("FULFILLMENT_EVENT_INVALID", "履约回调字段无效。", 422)
    if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None:
        raise DomainError("FULFILLMENT_EVENT_INVALID", "履约回调必须包含带时区的 occurred_at。", 422)
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        raise DomainError("FULFILLMENT_EVENT_INVALID", "履约回调 payload 必须为对象。", 422)
    digest = payload_digest(event)
    existing = db.scalar(select(InboxEvent).where(InboxEvent.provider == provider, InboxEvent.provider_event_id == event_id).with_for_update())
    if existing is not None:
        if existing.payload_hash != digest:
            raise DomainError("FULFILLMENT_EVENT_CONFLICT", "同一 Provider 事件 ID 的载荷不一致。", 409)
        db.commit()
        return existing
    case = db.scalar(select(AfterSalesCase).where(AfterSalesCase.id == case_id).with_for_update())
    if case is None:
        raise DomainError("CASE_NOT_FOUND", "履约回调关联的售后单不存在。", 404)
    inbox = InboxEvent(
        id=_new_id(), provider=provider, provider_event_id=event_id, case_id=case_id, event_type=event_type,
        sequence_no=sequence_no, payload=payload, payload_hash=digest, occurred_at=occurred_at,
    )
    db.add(inbox)
    db.flush()
    _apply_inbox_locked(db, inbox, case)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        existing = db.scalar(select(InboxEvent).where(InboxEvent.provider == provider, InboxEvent.provider_event_id == event_id))
        if existing is None:
            raise DomainError("FULFILLMENT_EVENT_CONFLICT", "履约事件并发写入失败，请重试。", 409) from error
        if existing.payload_hash != digest:
            raise DomainError("FULFILLMENT_EVENT_CONFLICT", "同一 Provider 事件 ID 的载荷不一致。", 409)
        return existing
    return inbox


def replay_deferred_event(db: Session, inbox_event_id: str, actor_id: str) -> InboxEvent:
    inbox = db.scalar(select(InboxEvent).where(InboxEvent.id == inbox_event_id).with_for_update())
    if inbox is None:
        raise DomainError("INBOX_EVENT_NOT_FOUND", "履约事件不存在。", 404)
    if inbox.status != "deferred":
        raise DomainError("INBOX_EVENT_NOT_DEFERRED", "只有等待中的履约事件可以重放。", 409)
    case = db.scalar(select(AfterSalesCase).where(AfterSalesCase.id == inbox.case_id).with_for_update())
    assert case is not None
    _apply_inbox_locked(db, inbox, case)
    _audit(db, case.id, "FULFILLMENT_EVENT_REPLAYED", f"运营人员 {actor_id} 重放事件 {inbox.provider_event_id}。", "operator", actor_id, inbox.id)
    db.commit()
    return inbox


def claim_one_outbox(db: Session, lease_seconds: int = 30) -> OutboxEvent | None:
    now = utcnow()
    candidate = db.scalar(
        select(OutboxEvent).where(
            or_(
                (OutboxEvent.status == "pending") & (OutboxEvent.next_attempt_at <= now),
                (OutboxEvent.status == "processing") & (OutboxEvent.lease_until < now),
            )
        ).order_by(OutboxEvent.created_at).with_for_update(skip_locked=True).limit(1)
    )
    if candidate is None:
        db.commit()
        return None
    candidate.status = "processing"
    candidate.attempts += 1
    candidate.lease_until = now + timedelta(seconds=lease_seconds)
    db.commit()
    return candidate


def process_one_outbox(
    db: Session, provider: FulfillmentProvider | None = None, notifier: NotificationGateway | None = None, *, max_attempts: int = 5,
) -> str | None:
    provider, notifier = provider or SimulatorFulfillmentProvider(), notifier or InAppNotificationGateway()
    event = claim_one_outbox(db)
    if event is None:
        return None
    try:
        if event.destination == "fulfillment_provider" and event.event_type == "pickup.requested":
            response = provider.request_pickup(event.payload, event.idempotency_key)
        elif event.destination == "notification_gateway" and event.event_type == "notification.requested":
            notification = db.get(CustomerNotification, event.payload["notification_id"])
            if notification is None:
                raise RuntimeError("NOTIFICATION_NOT_FOUND")
            response = notifier.send(notification, event.idempotency_key)
        else:
            raise RuntimeError("OUTBOX_DESTINATION_UNSUPPORTED")
    except Exception as error:
        locked = db.scalar(select(OutboxEvent).where(OutboxEvent.id == event.id).with_for_update())
        assert locked is not None
        db.add(OutboxDelivery(id=_new_id(), outbox_event_id=locked.id, attempt_no=locked.attempts,
            provider_idempotency_key=locked.idempotency_key, status="failed", error_code=type(error).__name__))
        locked.last_error = type(error).__name__
        locked.lease_until = None
        locked.status = "dead" if locked.attempts >= max_attempts else "pending"
        locked.next_attempt_at = utcnow() + timedelta(seconds=min(300, 2 ** min(locked.attempts, 8)))
        if locked.status == "dead" and locked.aggregate_type == "after_sales_case":
            _create_incident(db, int(locked.aggregate_id), "OUTBOX_DELIVERY_EXHAUSTED", "履约投递达到最大重试次数。", f"outbox-dead:{locked.id}")
        db.commit()
        return event.id
    locked = db.scalar(select(OutboxEvent).where(OutboxEvent.id == event.id).with_for_update())
    assert locked is not None
    db.add(OutboxDelivery(id=_new_id(), outbox_event_id=locked.id, attempt_no=locked.attempts,
        provider_idempotency_key=locked.idempotency_key, status="succeeded", response_payload=response))
    locked.status, locked.lease_until, locked.last_error, locked.delivered_at = "delivered", None, None, utcnow()
    if locked.destination == "notification_gateway":
        notification = db.get(CustomerNotification, locked.payload["notification_id"])
        if notification is not None:
            notification.status, notification.sent_at, notification.last_error = "sent", utcnow(), None
    db.commit()
    return event.id


def list_case_fulfillment(db: Session, case_id: int) -> tuple[list[FulfillmentEvent], list[CustomerNotification]]:
    events = list(db.scalars(select(FulfillmentEvent).where(FulfillmentEvent.case_id == case_id).order_by(FulfillmentEvent.sequence_no)))
    notifications = list(db.scalars(select(CustomerNotification).where(CustomerNotification.case_id == case_id).order_by(CustomerNotification.created_at)))
    return events, notifications


def list_incidents(db: Session, status: str | None = None) -> list[FulfillmentIncident]:
    query = select(FulfillmentIncident).order_by(FulfillmentIncident.created_at.desc())
    if status:
        query = query.where(FulfillmentIncident.status == status)
    return list(db.scalars(query))


def acknowledge_incident(db: Session, incident_id: str, actor_id: str, expected_version: int) -> FulfillmentIncident:
    incident = db.scalar(select(FulfillmentIncident).where(FulfillmentIncident.id == incident_id).with_for_update())
    if incident is None:
        raise DomainError("FULFILLMENT_INCIDENT_NOT_FOUND", "履约事故不存在。", 404)
    if incident.version != expected_version:
        raise DomainError("FULFILLMENT_INCIDENT_VERSION_CONFLICT", "事故已被其他运营人员更新，请刷新后重试。", 409)
    if incident.status != "open":
        raise DomainError("FULFILLMENT_INCIDENT_NOT_OPEN", "只有待处理事故可以领取。", 409)
    incident.status, incident.assigned_operator_id, incident.version = "acknowledged", actor_id, incident.version + 1
    _audit(db, incident.case_id, "FULFILLMENT_INCIDENT_ACKNOWLEDGED", f"运营人员 {actor_id} 领取履约事故 {incident.id}。", "operator", actor_id, incident.id)
    db.commit()
    return incident


def resolve_incident(db: Session, incident_id: str, actor_id: str, expected_version: int, note: str) -> FulfillmentIncident:
    incident = db.scalar(select(FulfillmentIncident).where(FulfillmentIncident.id == incident_id).with_for_update())
    if incident is None:
        raise DomainError("FULFILLMENT_INCIDENT_NOT_FOUND", "履约事故不存在。", 404)
    if incident.version != expected_version:
        raise DomainError("FULFILLMENT_INCIDENT_VERSION_CONFLICT", "事故已被其他运营人员更新，请刷新后重试。", 409)
    if incident.status not in {"open", "acknowledged"}:
        raise DomainError("FULFILLMENT_INCIDENT_ALREADY_RESOLVED", "事故已经解决。", 409)
    if incident.assigned_operator_id and incident.assigned_operator_id != actor_id:
        raise DomainError("FULFILLMENT_INCIDENT_ASSIGNEE_REQUIRED", "只有领取该事故的运营人员可以解决。", 403)
    incident.status, incident.assigned_operator_id, incident.resolution_note, incident.version = "resolved", actor_id, note, incident.version + 1
    _audit(db, incident.case_id, "FULFILLMENT_INCIDENT_RESOLVED", f"运营人员 {actor_id} 解决履约事故 {incident.id}：{note}", "operator", actor_id, incident.id)
    db.commit()
    return incident
