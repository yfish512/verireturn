from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AfterSalesCase, AuditLog, IdempotencyRecord, Logistics, Order
from ..schemas import AfterSalesCreateRequest, EligibilityRequest
from .policy import EligibilityDecision, evaluate_after_sales


class DomainError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


PENDING_CONFIRMATION = "pending_confirmation"
AWAITING_PICKUP = "awaiting_pickup"
PICKUP_SCHEDULED = "pickup_scheduled"
COMPLETED = "completed"
CANCELLED = "cancelled"
MANUAL_REVIEW = "manual_review"


def request_fingerprint(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def get_owned_order(db: Session, user_id: str, order_id: str) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise DomainError("ORDER_NOT_FOUND", "订单不存在。", 404)
    if order.user_id != user_id:
        raise DomainError("ORDER_ACCESS_DENIED", "无权查询或操作该订单。", 403)
    return order


def get_logistics(db: Session, user_id: str, order_id: str) -> Logistics:
    get_owned_order(db, user_id, order_id)
    logistics = db.get(Logistics, order_id)
    if logistics is None:
        raise DomainError("LOGISTICS_NOT_FOUND", "未找到物流信息。", 404)
    return logistics


def get_owned_case(db: Session, user_id: str, case_id: int) -> AfterSalesCase:
    case = db.get(AfterSalesCase, case_id)
    if case is None:
        raise DomainError("CASE_NOT_FOUND", "售后单不存在。", 404)
    if case.user_id != user_id:
        raise DomainError("CASE_ACCESS_DENIED", "无权操作该售后单。", 403)
    return case


def check_eligibility(db: Session, user_id: str, request: EligibilityRequest) -> EligibilityDecision:
    return evaluate_after_sales(get_owned_order(db, user_id, request.order_id), request.request_type)


def list_audit_logs(db: Session, user_id: str, case_id: int) -> list[AuditLog]:
    get_owned_case(db, user_id, case_id)
    return list(db.scalars(select(AuditLog).where(AuditLog.case_id == case_id).order_by(AuditLog.id)))


def _existing_idempotent_case(
    db: Session, actor_id: str, operation: str, idempotency_key: str, payload_hash: str, expected_case_id: int | None = None
) -> AfterSalesCase | None:
    record = db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.actor_id == actor_id,
        IdempotencyRecord.operation == operation,
        IdempotencyRecord.idempotency_key == idempotency_key,
    ))
    if record is None:
        return None
    if record.request_hash != payload_hash:
        raise DomainError("IDEMPOTENCY_KEY_CONFLICT", "同一幂等键不能用于不同请求。", 409)
    if expected_case_id is not None and record.resource_id != str(expected_case_id):
        raise DomainError("IDEMPOTENCY_KEY_CONFLICT", "同一幂等键不能操作不同售后单。", 409)
    case = db.get(AfterSalesCase, int(record.resource_id))
    if case is None:
        raise DomainError("IDEMPOTENCY_RECORD_INVALID", "幂等记录关联的售后单不存在。", 500)
    return case


def _record_idempotency(
    db: Session, actor_id: str, operation: str, idempotency_key: str, payload_hash: str, case_id: int
) -> None:
    db.add(IdempotencyRecord(
        actor_id=actor_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=payload_hash,
        resource_id=str(case_id),
    ))


def _audit(
    db: Session, case_id: int, event_type: str, detail: str, actor_type: str, actor_id: str, request_id: str | None
) -> None:
    db.add(AuditLog(
        case_id=case_id,
        event_type=event_type,
        detail=detail,
        actor_type=actor_type,
        actor_id=actor_id,
        request_id=request_id,
    ))


def _commit_or_replay(
    db: Session, actor_id: str, operation: str, idempotency_key: str, payload_hash: str, case_id: int,
    expected_case_id: int | None = None,
) -> AfterSalesCase:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        existing = _existing_idempotent_case(
            db, actor_id, operation, idempotency_key, payload_hash, expected_case_id
        )
        if existing is None:
            raise DomainError("TRANSACTION_CONFLICT", "事务提交失败，请使用相同幂等键重试。", 409) from error
        return existing
    case = db.get(AfterSalesCase, case_id)
    if case is None:
        raise DomainError("CASE_NOT_FOUND", "售后单不存在。", 404)
    return case


def create_case(
    db: Session, user_id: str, request: AfterSalesCreateRequest, idempotency_key: str, request_id: str | None = None
) -> AfterSalesCase:
    operation = "create_after_sales_case"
    payload_hash = request_fingerprint({"order_id": request.order_id, "request_type": request.request_type, "reason": request.reason})
    existing = _existing_idempotent_case(db, user_id, operation, idempotency_key, payload_hash)
    if existing is not None:
        return existing
    decision = check_eligibility(db, user_id, request)
    if not decision.eligible:
        raise DomainError(decision.policy_code, decision.explanation)
    try:
        case = AfterSalesCase(
            user_id=user_id, order_id=request.order_id, request_type=request.request_type, reason=request.reason,
            eligible_amount=decision.eligible_amount, operation=operation, idempotency_key=idempotency_key, request_hash=payload_hash,
        )
        db.add(case)
        db.flush()
        _audit(db, case.id, "CASE_CREATED", "售后单已创建，等待用户确认。", "customer", user_id, request_id)
        _record_idempotency(db, user_id, operation, idempotency_key, payload_hash, case.id)
        return _commit_or_replay(db, user_id, operation, idempotency_key, payload_hash, case.id)
    except IntegrityError as error:
        # PostgreSQL can raise at flush, before commit; replay through the same durable record.
        db.rollback()
        existing = _existing_idempotent_case(db, user_id, operation, idempotency_key, payload_hash)
        if existing is None:
            raise DomainError("TRANSACTION_CONFLICT", "事务创建失败，请使用相同幂等键重试。", 409) from error
        return existing


def _transition_case(
    db: Session, user_id: str, case_id: int, operation: str, idempotency_key: str, payload: dict[str, object],
    allowed_from: set[str], target_status: str, event_type: str, detail: str, request_id: str | None = None,
    actor_type: str = "customer", actor_id: str | None = None, internal: bool = False, pickup_slot: str | None = None,
    after_transition: Callable[[AfterSalesCase], None] | None = None,
    invalid_code: str = "INVALID_STATE_TRANSITION",
) -> AfterSalesCase:
    actor_id = actor_id or user_id
    payload_hash = request_fingerprint(payload)
    existing = _existing_idempotent_case(db, actor_id, operation, idempotency_key, payload_hash, case_id)
    if existing is not None:
        return existing

    case = db.scalar(select(AfterSalesCase).where(AfterSalesCase.id == case_id).with_for_update())
    if case is None:
        raise DomainError("CASE_NOT_FOUND", "售后单不存在。", 404)
    if not internal and case.user_id != user_id:
        raise DomainError("CASE_ACCESS_DENIED", "无权操作该售后单。", 403)
    if case.status == target_status:
        if pickup_slot is not None and case.pickup_slot != pickup_slot:
            raise DomainError("IDEMPOTENCY_KEY_CONFLICT", "取件时段已经确定，不能使用新请求覆盖。", 409)
        _record_idempotency(db, actor_id, operation, idempotency_key, payload_hash, case.id)
        return _commit_or_replay(db, actor_id, operation, idempotency_key, payload_hash, case.id, case.id)
    if case.status not in allowed_from:
        raise DomainError(invalid_code, f"售后单当前为 {case.status}，不能执行 {operation}。", 409)

    case.status = target_status
    if pickup_slot is not None:
        case.pickup_slot = pickup_slot
    if target_status == COMPLETED:
        case.completed_at = datetime.now(timezone.utc)
    if after_transition is not None:
        after_transition(case)
    _audit(db, case.id, event_type, detail, actor_type, actor_id, request_id)
    _record_idempotency(db, actor_id, operation, idempotency_key, payload_hash, case.id)
    return _commit_or_replay(db, actor_id, operation, idempotency_key, payload_hash, case.id, case.id)


def confirm_case(db: Session, user_id: str, case_id: int, idempotency_key: str, request_id: str | None = None) -> AfterSalesCase:
    return _transition_case(db, user_id, case_id, "confirm_after_sales_case", idempotency_key, {"case_id": case_id},
        {PENDING_CONFIRMATION}, AWAITING_PICKUP, "CASE_CONFIRMED", "用户已确认售后操作，等待预约取件。", request_id)


def cancel_case(db: Session, user_id: str, case_id: int, idempotency_key: str, request_id: str | None = None) -> AfterSalesCase:
    return _transition_case(db, user_id, case_id, "cancel_after_sales_case", idempotency_key, {"case_id": case_id},
        {PENDING_CONFIRMATION, AWAITING_PICKUP}, CANCELLED, "CASE_CANCELLED", "用户已取消售后操作。", request_id)


def schedule_pickup(
    db: Session, user_id: str, case_id: int, time_slot: str, idempotency_key: str, request_id: str | None = None
) -> AfterSalesCase:
    # The external pickup request is an intent, not a claim that a courier has
    # collected anything. Persist it in the same transaction as the M1 state.
    from .fulfillment import enqueue_pickup_request
    return _transition_case(db, user_id, case_id, "schedule_pickup", idempotency_key, {"case_id": case_id, "time_slot": time_slot},
        {AWAITING_PICKUP}, PICKUP_SCHEDULED, "PICKUP_SCHEDULED", f"取件时段：{time_slot}", request_id,
        pickup_slot=time_slot, invalid_code="CASE_NOT_CONFIRMED",
        after_transition=lambda case: enqueue_pickup_request(db, case, idempotency_key))


def complete_case(db: Session, case_id: int, idempotency_key: str, request_id: str | None = None) -> AfterSalesCase:
    return _transition_case(db, "", case_id, "complete_after_sales_case", idempotency_key, {"case_id": case_id},
        {PICKUP_SCHEDULED}, COMPLETED, "CASE_COMPLETED", "物流回调确认商品已签收，售后流程完成。", request_id,
        actor_type="system", actor_id="logistics-simulator", internal=True)


def mark_manual_review(
    db: Session, case_id: int, reason: str, idempotency_key: str, request_id: str | None = None
) -> AfterSalesCase:
    return _transition_case(db, "", case_id, "mark_manual_review", idempotency_key, {"case_id": case_id, "reason": reason},
        {PENDING_CONFIRMATION, AWAITING_PICKUP, PICKUP_SCHEDULED}, MANUAL_REVIEW, "CASE_MANUAL_REVIEW", reason, request_id,
        actor_type="system", actor_id="ops-simulator", internal=True)
