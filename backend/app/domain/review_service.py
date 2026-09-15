"""M3 review workflow commands.

This module owns review tickets only. It deliberately never reopens a M1 case
in ``manual_review``; an approved exception creates a separate, customer-
confirmable case in the same database transaction as the review decision.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import os
from pathlib import Path
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Actor, AfterSalesCase, AuditLog, IdempotencyRecord, PolicyVersion, ReviewEvent, ReviewTicket, ReviewUpload
from ..schemas import PolicyVersionPublishRequest, ReviewDecisionRequest, ReviewTicketCreateRequest
from .policy import evaluate_review_disposition
from .service import DomainError, PENDING_CONFIRMATION, get_owned_order, request_fingerprint


def _idempotent_ticket(
    db: Session, actor_id: str, operation: str, idempotency_key: str, payload_hash: str, expected_ticket_id: str | None = None
) -> ReviewTicket | None:
    record = db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.actor_id == actor_id,
        IdempotencyRecord.operation == operation,
        IdempotencyRecord.idempotency_key == idempotency_key,
    ))
    if record is None:
        return None
    if record.request_hash != payload_hash:
        raise DomainError("IDEMPOTENCY_KEY_CONFLICT", "同一幂等键不能用于不同审核请求。", 409)
    if expected_ticket_id is not None and record.resource_id != expected_ticket_id:
        raise DomainError("IDEMPOTENCY_KEY_CONFLICT", "同一幂等键不能操作不同审核工单。", 409)
    ticket = db.get(ReviewTicket, record.resource_id)
    if ticket is None:
        raise DomainError("IDEMPOTENCY_RECORD_INVALID", "幂等记录关联的审核工单不存在。", 500)
    return ticket


def _record_idempotency(db: Session, actor_id: str, operation: str, key: str, payload_hash: str, ticket_id: str) -> None:
    db.add(IdempotencyRecord(
        actor_id=actor_id, operation=operation, idempotency_key=key, request_hash=payload_hash,
        resource_type="review_ticket", resource_id=ticket_id,
    ))


def _append_event(
    db: Session, ticket: ReviewTicket, event_type: str, payload: dict, actor_id: str, actor_role: str, request_id: str | None
) -> None:
    next_sequence = db.scalar(select(ReviewEvent.sequence_no).where(ReviewEvent.ticket_id == ticket.id).order_by(ReviewEvent.sequence_no.desc()).limit(1))
    db.add(ReviewEvent(
        ticket_id=ticket.id, sequence_no=(next_sequence or 0) + 1, event_type=event_type, payload=payload,
        actor_id=actor_id, actor_role=actor_role, request_id=request_id,
    ))


def _published_policy(db: Session) -> PolicyVersion:
    policy = db.scalar(select(PolicyVersion).where(PolicyVersion.status == "published").order_by(PolicyVersion.published_at.desc()))
    if policy is None:
        raise DomainError("PUBLISHED_POLICY_NOT_FOUND", "没有可用的已发布售后策略。", 503)
    return policy


def list_policy_versions(db: Session) -> list[PolicyVersion]:
    return list(db.scalars(select(PolicyVersion).order_by(PolicyVersion.created_at.desc())))


def publish_policy_version(
    db: Session, manager_id: str, request: PolicyVersionPublishRequest, idempotency_key: str, request_id: str | None = None,
) -> PolicyVersion:
    """Atomically publish a new immutable policy configuration.

    Existing published rows are only retired; their configuration checksum and
    payload remain untouched for historical replay.
    """
    operation = "publish_policy_version"
    rules = {
        "quality_dispute_enabled": request.quality_dispute_enabled,
        "quality_dispute_terms": request.quality_dispute_terms,
        "review_sla_hours": request.review_sla_hours,
    }
    payload_hash = request_fingerprint({"version": request.version, "rules": rules})
    record = db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.actor_id == manager_id, IdempotencyRecord.operation == operation,
        IdempotencyRecord.idempotency_key == idempotency_key,
    ))
    if record is not None:
        if record.request_hash != payload_hash:
            raise DomainError("IDEMPOTENCY_KEY_CONFLICT", "同一幂等键不能发布不同策略。", 409)
        replay = db.get(PolicyVersion, record.resource_id)
        if replay is None:
            raise DomainError("IDEMPOTENCY_RECORD_INVALID", "幂等记录关联的策略版本不存在。", 500)
        return replay
    if db.scalar(select(PolicyVersion.id).where(PolicyVersion.version == request.version)) is not None:
        raise DomainError("POLICY_VERSION_EXISTS", "策略版本号已经存在。", 409)
    now = datetime.now(timezone.utc)
    try:
        # Lock a stable row before inspecting the currently published policy.
        # Locking only that policy permits a waiting transaction to observe it
        # after retirement and insert a second published version.
        manager = db.scalar(select(Actor).where(Actor.id == manager_id).with_for_update())
        if manager is None or manager.role != "ops_manager" or not manager.active:
            raise DomainError("OPS_MANAGER_REQUIRED", "需要运营主管权限。", 403)
        for prior in db.scalars(select(PolicyVersion).where(PolicyVersion.status == "published").with_for_update()):
            prior.status = "retired"
        # The partial unique index allows one published policy. Force the
        # retirement update before inserting a replacement; relying on ORM
        # flush ordering makes concurrent publishers intermittently conflict.
        db.flush()
        policy = PolicyVersion(
            id=str(uuid4()), version=request.version, status="published", rules_json=rules, checksum=request_fingerprint(rules),
            published_by=manager_id, published_at=now,
        )
        db.add(policy)
        db.flush()
        _record_idempotency(db, manager_id, operation, idempotency_key, payload_hash, policy.id)
        db.commit()
        return policy
    except IntegrityError as error:
        db.rollback()
        raise DomainError("TRANSACTION_CONFLICT", "策略发布冲突，请使用相同幂等键重试。", 409) from error


def _commit_or_replay(
    db: Session, actor_id: str, operation: str, key: str, payload_hash: str, ticket_id: str
) -> ReviewTicket:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        replay = _idempotent_ticket(db, actor_id, operation, key, payload_hash, ticket_id)
        if replay is None:
            raise DomainError("TRANSACTION_CONFLICT", "审核事务冲突，请使用相同幂等键重试。", 409) from error
        return replay
    ticket = db.get(ReviewTicket, ticket_id)
    if ticket is None:
        raise DomainError("REVIEW_TICKET_NOT_FOUND", "审核工单不存在。", 404)
    return ticket


def create_review_ticket(
    db: Session, requester_id: str, request: ReviewTicketCreateRequest, idempotency_key: str, request_id: str | None = None,
    source_case_id: int | None = None, source_run_id: str | None = None,
) -> ReviewTicket:
    operation = "create_review_ticket"
    payload = {"order_id": request.order_id, "request_type": request.request_type, "reason": request.reason, "source_case_id": source_case_id}
    payload_hash = request_fingerprint(payload)
    replay = _idempotent_ticket(db, requester_id, operation, idempotency_key, payload_hash)
    if replay is not None:
        return replay
    order = get_owned_order(db, requester_id, request.order_id)
    policy = _published_policy(db)
    disposition = evaluate_review_disposition(order, request.request_type, request.reason, rules=policy.rules_json)
    if disposition.disposition == "auto_approve":
        raise DomainError("AUTO_PROCESS_AVAILABLE", "该请求满足自动售后条件，请直接发起售后申请。", 409)
    if disposition.disposition != "manual_review":
        raise DomainError("REVIEW_NOT_ALLOWED", f"该请求不进入人工审核：{disposition.explanation}", 400)
    ticket = ReviewTicket(
        id=str(uuid4()), requester_id=requester_id, order_id=order.id, source_case_id=source_case_id, source_run_id=source_run_id,
        request_type=request.request_type, reason=request.reason,
        request_snapshot={"order_id": order.id, "request_type": request.request_type, "reason": request.reason},
        trigger_code=disposition.policy_code, priority=disposition.priority, status="open",
        due_at=datetime.now(timezone.utc) + timedelta(hours=int(policy.rules_json.get("review_sla_hours", 24))), version=1, policy_version_id=policy.id,
    )
    db.add(ticket)
    db.flush()
    _append_event(
        db, ticket, "REVIEW_TICKET_CREATED", {"trigger_code": ticket.trigger_code, "policy_version_id": ticket.policy_version_id},
        requester_id, "customer", request_id,
    )
    _record_idempotency(db, requester_id, operation, idempotency_key, payload_hash, ticket.id)
    return _commit_or_replay(db, requester_id, operation, idempotency_key, payload_hash, ticket.id)


def get_review_ticket(db: Session, ticket_id: str) -> ReviewTicket:
    ticket = db.get(ReviewTicket, ticket_id)
    if ticket is None:
        raise DomainError("REVIEW_TICKET_NOT_FOUND", "审核工单不存在。", 404)
    return ticket


def get_owned_review_ticket(db: Session, ticket_id: str, requester_id: str) -> ReviewTicket:
    ticket = get_review_ticket(db, ticket_id)
    if ticket.requester_id != requester_id:
        raise DomainError("REVIEW_TICKET_ACCESS_DENIED", "无权查看或操作该审核工单。", 403)
    return ticket


def list_review_events(db: Session, ticket_id: str) -> list[ReviewEvent]:
    get_review_ticket(db, ticket_id)
    return list(db.scalars(select(ReviewEvent).where(ReviewEvent.ticket_id == ticket_id).order_by(ReviewEvent.sequence_no)))


def list_review_tickets(db: Session, status: str | None = None) -> list[ReviewTicket]:
    stmt = select(ReviewTicket).order_by(ReviewTicket.priority.desc(), ReviewTicket.due_at, ReviewTicket.created_at)
    if status:
        stmt = stmt.where(ReviewTicket.status == status)
    return list(db.scalars(stmt))


def _locked_ticket(db: Session, ticket_id: str, expected_version: int) -> ReviewTicket:
    ticket = db.scalar(select(ReviewTicket).where(ReviewTicket.id == ticket_id).with_for_update())
    if ticket is None:
        raise DomainError("REVIEW_TICKET_NOT_FOUND", "审核工单不存在。", 404)
    if ticket.version != expected_version:
        raise DomainError("REVIEW_VERSION_CONFLICT", "审核工单已被其他操作更新，请刷新后重试。", 409)
    return ticket


def claim_review_ticket(
    db: Session, ticket_id: str, operator_id: str, operator_role: str, expected_version: int, idempotency_key: str, request_id: str | None = None,
) -> ReviewTicket:
    operation = "claim_review_ticket"
    payload_hash = request_fingerprint({"ticket_id": ticket_id, "expected_version": expected_version})
    replay = _idempotent_ticket(db, operator_id, operation, idempotency_key, payload_hash, ticket_id)
    if replay is not None:
        return replay
    ticket = _locked_ticket(db, ticket_id, expected_version)
    if ticket.status != "open":
        raise DomainError("REVIEW_TICKET_NOT_OPEN", "审核工单当前不能领取。", 409)
    ticket.status = "claimed"
    ticket.assigned_operator_id = operator_id
    ticket.version += 1
    _append_event(db, ticket, "REVIEW_TICKET_CLAIMED", {}, operator_id, operator_role, request_id)
    _record_idempotency(db, operator_id, operation, idempotency_key, payload_hash, ticket.id)
    return _commit_or_replay(db, operator_id, operation, idempotency_key, payload_hash, ticket.id)


def supplement_review_ticket(
    db: Session, ticket_id: str, requester_id: str, content: str, expected_version: int, idempotency_key: str, request_id: str | None = None,
) -> ReviewTicket:
    operation = "supplement_review_ticket"
    payload_hash = request_fingerprint({"ticket_id": ticket_id, "content": content, "expected_version": expected_version})
    replay = _idempotent_ticket(db, requester_id, operation, idempotency_key, payload_hash, ticket_id)
    if replay is not None:
        return replay
    ticket = _locked_ticket(db, ticket_id, expected_version)
    if ticket.requester_id != requester_id:
        raise DomainError("REVIEW_TICKET_ACCESS_DENIED", "无权补充该审核工单。", 403)
    if ticket.status != "waiting_customer":
        raise DomainError("REVIEW_TICKET_NOT_WAITING_CUSTOMER", "当前不需要补充材料。", 409)
    ticket.status = "claimed" if ticket.assigned_operator_id else "open"
    ticket.version += 1
    _append_event(db, ticket, "CUSTOMER_SUPPLEMENTED", {"content": content}, requester_id, "customer", request_id)
    _record_idempotency(db, requester_id, operation, idempotency_key, payload_hash, ticket.id)
    return _commit_or_replay(db, requester_id, operation, idempotency_key, payload_hash, ticket.id)


def decide_review_ticket(
    db: Session, ticket_id: str, operator_id: str, operator_role: str, request: ReviewDecisionRequest,
    idempotency_key: str, request_id: str | None = None,
) -> ReviewTicket:
    operation = "decide_review_ticket"
    payload = request.model_dump()
    payload["ticket_id"] = ticket_id
    payload_hash = request_fingerprint(payload)
    replay = _idempotent_ticket(db, operator_id, operation, idempotency_key, payload_hash, ticket_id)
    if replay is not None:
        return replay
    ticket = _locked_ticket(db, ticket_id, request.expected_version)
    if ticket.status != "claimed":
        raise DomainError("REVIEW_TICKET_NOT_CLAIMED", "运营人员只能决策已领取的工单。", 409)
    if operator_role != "ops_manager" and ticket.assigned_operator_id != operator_id:
        raise DomainError("REVIEW_TICKET_ASSIGNEE_REQUIRED", "只有当前领取人可以处理该审核工单。", 403)

    event_payload = {"action": request.action, "reason_code": request.reason_code, "customer_message": request.customer_message}
    if request.action == "request_more_info":
        ticket.status = "waiting_customer"
        event_type = "REVIEW_MORE_INFO_REQUESTED"
    elif request.action == "reject":
        ticket.status = "rejected"
        event_type = "REVIEW_REJECTED"
    elif request.action == "close_duplicate":
        ticket.status = "closed"
        event_type = "REVIEW_DUPLICATE_CLOSED"
    else:
        order = get_owned_order(db, ticket.requester_id, ticket.order_id)
        replacement = AfterSalesCase(
            user_id=ticket.requester_id, order_id=ticket.order_id, request_type=ticket.request_type, reason=ticket.reason,
            status=PENDING_CONFIRMATION, eligible_amount=Decimal(order.amount), operation="create_from_review_ticket",
            idempotency_key=f"review-ticket:{ticket.id}", request_hash=request_fingerprint(ticket.request_snapshot),
            policy_version_id=ticket.policy_version_id, source_review_ticket_id=ticket.id,
        )
        db.add(replacement)
        db.flush()
        db.add(AuditLog(
            case_id=replacement.id, event_type="CASE_CREATED_FROM_REVIEW", detail=f"审核工单 {ticket.id} 已批准例外，等待客户确认。",
            actor_type="operator", actor_id=operator_id, request_id=request_id,
        ))
        ticket.status = "approved"
        event_type = "REVIEW_EXCEPTION_APPROVED"
        event_payload["superseding_case_id"] = replacement.id
    ticket.version += 1
    _append_event(db, ticket, event_type, event_payload, operator_id, operator_role, request_id)
    _record_idempotency(db, operator_id, operation, idempotency_key, payload_hash, ticket.id)
    return _commit_or_replay(db, operator_id, operation, idempotency_key, payload_hash, ticket.id)


def create_review_upload(db: Session, actor_id: str, filename: str, media_type: str, content_base64: str) -> ReviewUpload:
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except Exception as error:
        raise DomainError("ATTACHMENT_CONTENT_INVALID", "附件内容不是有效 Base64。", 422) from error
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise DomainError("ATTACHMENT_SIZE_INVALID", "附件必须介于 1 B 和 5 MB。", 422)
    if media_type not in {"image/jpeg", "image/png", "application/pdf"}:
        raise DomainError("ATTACHMENT_MEDIA_TYPE_INVALID", "仅支持 JPEG、PNG 或 PDF 审核材料。", 422)
    digest = hashlib.sha256(raw).hexdigest()
    existing = db.scalar(select(ReviewUpload).where(ReviewUpload.uploaded_by == actor_id, ReviewUpload.content_hash == digest))
    if existing is not None:
        return existing
    upload_id = str(uuid4())
    root = Path(os.getenv("REVIEW_UPLOAD_DIR", "data/review-uploads"))
    target = root / actor_id / upload_id
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, target)
    row = ReviewUpload(id=upload_id, uploaded_by=actor_id, object_ref=f"uploads/{actor_id}/{upload_id}", content_hash=digest, media_type=media_type, byte_size=len(raw), status="ready")
    db.add(row); db.commit()
    return row

def attach_review_evidence(db: Session, ticket_id: str, actor_id: str, object_ref: str, content_hash: str, media_type: str, expected_version: int, request_id: str | None = None):
    if not object_ref.startswith(f"uploads/{actor_id}/"):
        raise DomainError("ATTACHMENT_REFERENCE_DENIED", "附件必须使用当前客户的受控上传引用。", 403)
    if media_type.lower() not in {"image/jpeg", "image/png", "application/pdf"}:
        raise DomainError("ATTACHMENT_MEDIA_TYPE_INVALID", "仅支持 JPEG、PNG 或 PDF 审核材料。", 422)
    upload = db.scalar(select(ReviewUpload).where(ReviewUpload.object_ref == object_ref).with_for_update())
    if upload is None or upload.uploaded_by != actor_id or upload.status != "ready" or upload.content_hash != content_hash.lower() or upload.media_type != media_type:
        raise DomainError("ATTACHMENT_UPLOAD_NOT_VERIFIED", "附件必须先经受控上传并且校验通过。", 409)
    from ..models import ReviewAttachment
    ticket = db.scalar(select(ReviewTicket).where(ReviewTicket.id == ticket_id).with_for_update())
    if ticket is None: raise DomainError("REVIEW_TICKET_NOT_FOUND", "审核单不存在。", 404)
    if ticket.requester_id != actor_id: raise DomainError("REVIEW_TICKET_ACCESS_DENIED", "无权上传该审核单材料。", 403)
    if ticket.version != expected_version: raise DomainError("REVIEW_VERSION_CONFLICT", "审核单已更新，请刷新后重试。", 409)
    existing = db.scalar(select(ReviewAttachment).where(ReviewAttachment.ticket_id == ticket_id, ReviewAttachment.content_hash == content_hash))
    if existing is not None: return existing
    attachment = ReviewAttachment(id=str(uuid4()), ticket_id=ticket_id, uploaded_by=actor_id, object_ref=object_ref, content_hash=content_hash.lower(), media_type=media_type, version=ticket.version + 1)
    db.add(attachment); ticket.version += 1; ticket.status = "waiting_customer" if ticket.status == "open" else ticket.status
    _append_event(db, ticket, "CUSTOMER_ATTACHMENT_ADDED", {"attachment_id":attachment.id,"object_ref":object_ref,"content_hash":content_hash,"media_type":media_type}, actor_id, "customer", request_id)
    db.commit(); return attachment

def expire_overdue_review_tickets(db: Session, now: datetime | None = None) -> int:
    """SLA projection: terminally expire overdue tickets and leave an audit event.

    A worker may run this repeatedly; row locks and the terminal-state predicate make
    the transition idempotent across worker restarts.
    """
    now = now or datetime.now(timezone.utc)
    tickets = list(db.scalars(select(ReviewTicket).where(
        ReviewTicket.status.in_(("open", "claimed", "waiting_customer")), ReviewTicket.due_at < now
    ).with_for_update()))
    for ticket in tickets:
        prior = ticket.status
        ticket.status = "expired"; ticket.version += 1
        _append_event(db, ticket, "REVIEW_SLA_EXPIRED", {"previous_status": prior, "due_at": ticket.due_at.isoformat()}, "review-sla-worker", "internal_service", None)
    db.commit()
    return len(tickets)
