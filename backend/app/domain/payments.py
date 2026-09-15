"""Durable refund intent ledger; payment-provider facts settle refunds."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from ..models import AfterSalesCase, AfterSalesItem, OrderItem, PaymentProviderEvent, PaymentReconciliationItem, PaymentReconciliationRun, PaymentTransaction, RefundAttempt, RefundIntent
from .service import DomainError, _audit


def _digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

def ensure_refund_intent(db: Session, case: AfterSalesCase) -> RefundIntent:
    if case.request_type != "refund": raise DomainError("REFUND_NOT_APPLICABLE", "只有退款售后单可以创建退款意图。", 409)
    existing = db.scalar(select(RefundIntent).where(RefundIntent.case_id == case.id))
    if existing is not None: return existing
    transaction = db.scalar(select(PaymentTransaction).where(PaymentTransaction.order_id == case.order_id).with_for_update())
    if transaction is None:
        transaction = PaymentTransaction(id=str(uuid4()), order_id=case.order_id, user_id=case.user_id, provider="demo_payment", provider_payment_id=f"pay-{case.order_id}", amount=case.eligible_amount, status="captured")
        db.add(transaction); db.flush()
    if Decimal(transaction.amount) < Decimal(case.eligible_amount): raise DomainError("REFUND_AMOUNT_EXCEEDS_CAPTURE", "退款金额超过原支付金额。", 409)
    intent = RefundIntent(id=str(uuid4()), case_id=case.id, payment_transaction_id=transaction.id, amount=case.eligible_amount, provider=transaction.provider, idempotency_key=f"refund-case-{case.id}", status="pending")
    db.add(intent); _audit(db, case.id, "REFUND_INTENT_CREATED", f"退款意图 {intent.id} 已创建，等待支付渠道提交。", "system", "payment-ledger", intent.id)
    return intent

def submit_refund(db: Session, intent_id: str) -> RefundIntent:
    intent = db.scalar(select(RefundIntent).where(RefundIntent.id == intent_id).with_for_update())
    if intent is None: raise DomainError("REFUND_INTENT_NOT_FOUND", "退款意图不存在。", 404)
    if intent.status in {"submitted", "succeeded"}: return intent
    attempt = int(db.scalar(select(func.max(RefundAttempt.attempt_no)).where(RefundAttempt.refund_intent_id == intent.id)) or 0) + 1
    intent.status, intent.provider_refund_id = "submitted", intent.provider_refund_id or f"refund-{intent.id}"
    case = db.get(AfterSalesCase, intent.case_id); assert case is not None
    case.status = "refund_processing"
    db.add(RefundAttempt(id=str(uuid4()), refund_intent_id=intent.id, attempt_no=attempt, status="submitted", provider_response={"provider_refund_id": intent.provider_refund_id}))
    _audit(db, case.id, "REFUND_SUBMITTED", "退款请求已提交支付渠道。", "system", "payment-ledger", intent.id); db.commit(); return intent

def apply_refund_settlement(db: Session, provider: str, provider_refund_id: str, succeeded: bool, event_id: str, payload: dict | None = None) -> RefundIntent:
    payload = payload or {}; digest = _digest({"provider_refund_id": provider_refund_id, "succeeded": succeeded, "payload": payload})
    existing_event = db.scalar(select(PaymentProviderEvent).where(PaymentProviderEvent.provider == provider, PaymentProviderEvent.provider_event_id == event_id).with_for_update())
    if existing_event is not None:
        if existing_event.payload_hash != digest: raise DomainError("PAYMENT_EVENT_CONFLICT", "同一支付事件载荷不一致。", 409)
        return db.get(RefundIntent, existing_event.refund_intent_id)
    intent = db.scalar(select(RefundIntent).where(RefundIntent.provider == provider, RefundIntent.provider_refund_id == provider_refund_id).with_for_update())
    if intent is None: raise DomainError("REFUND_PROVIDER_REFERENCE_NOT_FOUND", "支付回调未关联退款意图。", 404)
    db.add(PaymentProviderEvent(id=str(uuid4()), provider=provider, provider_event_id=event_id, refund_intent_id=intent.id, payload_hash=digest, outcome="succeeded" if succeeded else "failed", payload=payload))
    if intent.status != "succeeded":
        intent.status, intent.failure_code, intent.settled_at = ("succeeded", None, datetime.now(timezone.utc)) if succeeded else ("failed", "PROVIDER_REFUND_FAILED", datetime.now(timezone.utc))
        attempt = int(db.scalar(select(func.max(RefundAttempt.attempt_no)).where(RefundAttempt.refund_intent_id == intent.id)) or 0) + 1
        db.add(RefundAttempt(id=str(uuid4()), refund_intent_id=intent.id, attempt_no=attempt, status=intent.status, provider_response={"event_id": event_id}))
        case = db.get(AfterSalesCase, intent.case_id); transaction = db.get(PaymentTransaction, intent.payment_transaction_id); assert case and transaction
        if succeeded:
            case.status, case.completed_at, transaction.status = "completed", datetime.now(timezone.utc), "refunded"
            for line in db.scalars(select(AfterSalesItem).where(AfterSalesItem.case_id == case.id)):
                order_item = db.scalar(select(OrderItem).where(OrderItem.id == line.order_item_id).with_for_update())
                assert order_item is not None
                if order_item.refunded_quantity + line.quantity > order_item.quantity:
                    raise DomainError("REFUND_QUANTITY_EXCEEDED", "退款数量超过订单商品数量。", 409)
                order_item.refunded_quantity += line.quantity
            _audit(db, case.id, "REFUND_SETTLED", "支付渠道确认退款成功。", "internal_service", provider, event_id)
        else: _audit(db, case.id, "REFUND_FAILED", "支付渠道返回退款失败，等待运营处理。", "internal_service", provider, event_id)
    db.commit(); return intent

def reconcile_refunds(db: Session, provider: str, provider_statuses: dict[str, str]) -> PaymentReconciliationRun:
    run = PaymentReconciliationRun(id=str(uuid4()), provider=provider, status="completed", completed_at=datetime.now(timezone.utc)); db.add(run)
    for intent in db.scalars(select(RefundIntent).where(RefundIntent.provider == provider, RefundIntent.status.in_(("submitted", "succeeded")))):
        remote = provider_statuses.get(intent.provider_refund_id or "")
        status = "matched" if remote == intent.status else "missing_at_provider" if remote is None else "status_mismatch"
        db.add(PaymentReconciliationItem(id=str(uuid4()), run_id=run.id, refund_intent_id=intent.id, status=status, provider_status=remote, detail=None if status == "matched" else "渠道与本地退款状态不一致。"))
    db.commit(); return run
