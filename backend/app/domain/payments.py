"""Durable refund intent ledger; provider callbacks are the settlement source."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AfterSalesCase, PaymentTransaction, RefundAttempt, RefundIntent
from .service import DomainError, _audit


def ensure_refund_intent(db: Session, case: AfterSalesCase) -> RefundIntent:
    if case.request_type != "refund":
        raise DomainError("REFUND_NOT_APPLICABLE", "只有退款售后单可以创建退款意图。", 409)
    existing = db.scalar(select(RefundIntent).where(RefundIntent.case_id == case.id))
    if existing is not None:
        return existing
    transaction = db.scalar(select(PaymentTransaction).where(PaymentTransaction.order_id == case.order_id).with_for_update())
    if transaction is None:
        transaction = PaymentTransaction(
            id=str(uuid4()), order_id=case.order_id, user_id=case.user_id, provider="demo_payment",
            provider_payment_id=f"pay-{case.order_id}", amount=case.eligible_amount, status="captured",
        )
        db.add(transaction)
        db.flush()
    if Decimal(transaction.amount) < Decimal(case.eligible_amount):
        raise DomainError("REFUND_AMOUNT_EXCEEDS_CAPTURE", "退款金额超过原支付金额。", 409)
    intent = RefundIntent(
        id=str(uuid4()), case_id=case.id, payment_transaction_id=transaction.id, amount=case.eligible_amount,
        provider=transaction.provider, idempotency_key=f"refund-case-{case.id}", status="pending",
    )
    db.add(intent)
    _audit(db, case.id, "REFUND_INTENT_CREATED", f"退款意图 {intent.id} 已创建，等待支付渠道提交。", "system", "payment-ledger", intent.id)
    return intent


def submit_refund(db: Session, intent_id: str) -> RefundIntent:
    intent = db.scalar(select(RefundIntent).where(RefundIntent.id == intent_id).with_for_update())
    if intent is None:
        raise DomainError("REFUND_INTENT_NOT_FOUND", "退款意图不存在。", 404)
    if intent.status in {"submitted", "succeeded"}:
        return intent
    attempt = int(db.scalar(select(func.max(RefundAttempt.attempt_no)).where(RefundAttempt.refund_intent_id == intent.id)) or 0) + 1
    intent.status, intent.provider_refund_id = "submitted", intent.provider_refund_id or f"refund-{intent.id}"
    db.add(RefundAttempt(id=str(uuid4()), refund_intent_id=intent.id, attempt_no=attempt, status="submitted", provider_response={"provider_refund_id": intent.provider_refund_id}))
    db.commit()
    return intent


def apply_refund_settlement(db: Session, provider: str, provider_refund_id: str, succeeded: bool, event_id: str) -> RefundIntent:
    intent = db.scalar(select(RefundIntent).where(RefundIntent.provider == provider, RefundIntent.provider_refund_id == provider_refund_id).with_for_update())
    if intent is None:
        raise DomainError("REFUND_PROVIDER_REFERENCE_NOT_FOUND", "支付回调未关联退款意图。", 404)
    if intent.status == "succeeded":
        return intent
    intent.status, intent.failure_code = ("succeeded", None) if succeeded else ("failed", "PROVIDER_REFUND_FAILED")
    intent.settled_at = datetime.now(timezone.utc)
    db.add(RefundAttempt(id=str(uuid4()), refund_intent_id=intent.id, attempt_no=int(db.scalar(select(func.max(RefundAttempt.attempt_no)).where(RefundAttempt.refund_intent_id == intent.id)) or 0) + 1, status=intent.status, provider_response={"event_id": event_id}))
    db.commit()
    return intent
