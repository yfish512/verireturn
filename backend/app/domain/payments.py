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
    reserved = db.scalar(select(func.coalesce(func.sum(RefundIntent.amount), 0)).where(
        RefundIntent.payment_transaction_id == transaction.id,
        RefundIntent.status.in_(("pending", "submitted", "succeeded")),
    )) or Decimal("0")
    if Decimal(transaction.amount) < Decimal(reserved) + Decimal(case.eligible_amount):
        raise DomainError("REFUND_AMOUNT_EXCEEDS_CAPTURE", "累计退款金额超过原支付金额。", 409)
    intent = RefundIntent(id=str(uuid4()), case_id=case.id, payment_transaction_id=transaction.id, amount=case.eligible_amount, provider=transaction.provider, idempotency_key=f"refund-case-{case.id}", status="pending")
    db.add(intent); db.flush(); enqueue_refund_submission(db, intent)
    _audit(db, case.id, "REFUND_INTENT_CREATED", f"退款意图 {intent.id} 已创建，等待支付渠道提交。", "system", "payment-ledger", intent.id)
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
    payload = payload or {}
    try:
        amount, currency = Decimal(str(payload["amount"])), str(payload["currency"])
        occurred_at = datetime.fromisoformat(str(payload["occurred_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError, ArithmeticError) as error:
        raise DomainError("PAYMENT_EVENT_FACTS_INVALID", "支付回调必须包含金额、币种和发生时间。", 422) from error
    digest = _digest({"provider_refund_id": provider_refund_id, "succeeded": succeeded, "payload": payload})
    existing_event = db.scalar(select(PaymentProviderEvent).where(PaymentProviderEvent.provider == provider, PaymentProviderEvent.provider_event_id == event_id).with_for_update())
    if existing_event is not None:
        if existing_event.payload_hash != digest: raise DomainError("PAYMENT_EVENT_CONFLICT", "同一支付事件载荷不一致。", 409)
        return db.get(RefundIntent, existing_event.refund_intent_id)
    intent = db.scalar(select(RefundIntent).where(RefundIntent.provider == provider, RefundIntent.provider_refund_id == provider_refund_id).with_for_update())
    if intent is None: raise DomainError("REFUND_PROVIDER_REFERENCE_NOT_FOUND", "支付回调未关联退款意图。", 404)
    if amount != Decimal(intent.amount) or currency != intent.currency:
        raise DomainError("PAYMENT_AMOUNT_MISMATCH", "支付回调金额或币种与退款意图不一致。", 422)
    db.add(PaymentProviderEvent(id=str(uuid4()), provider=provider, provider_event_id=event_id, refund_intent_id=intent.id, payload_hash=digest, outcome="succeeded" if succeeded else "failed", amount=amount, currency=currency, occurred_at=occurred_at, payload=payload))
    if intent.status != "succeeded":
        intent.status, intent.failure_code, intent.settled_at = ("succeeded", None, datetime.now(timezone.utc)) if succeeded else ("failed", "PROVIDER_REFUND_FAILED", datetime.now(timezone.utc))
        attempt = int(db.scalar(select(func.max(RefundAttempt.attempt_no)).where(RefundAttempt.refund_intent_id == intent.id)) or 0) + 1
        db.add(RefundAttempt(id=str(uuid4()), refund_intent_id=intent.id, attempt_no=attempt, status=intent.status, provider_response={"event_id": event_id}))
        case = db.get(AfterSalesCase, intent.case_id); transaction = db.get(PaymentTransaction, intent.payment_transaction_id); assert case and transaction
        if succeeded:
            case.status, case.completed_at = "completed", datetime.now(timezone.utc)
            for line in db.scalars(select(AfterSalesItem).where(AfterSalesItem.case_id == case.id)):
                order_item = db.scalar(select(OrderItem).where(OrderItem.id == line.order_item_id).with_for_update())
                assert order_item is not None
                if order_item.refunded_quantity + line.quantity > order_item.quantity:
                    raise DomainError("REFUND_QUANTITY_EXCEEDED", "退款数量超过订单商品数量。", 409)
                order_item.refunded_quantity += line.quantity
            refunded_total = db.scalar(select(func.coalesce(func.sum(RefundIntent.amount), 0)).where(RefundIntent.payment_transaction_id == transaction.id, RefundIntent.status == "succeeded")) or 0
            transaction.status = "refunded" if Decimal(refunded_total) >= Decimal(transaction.amount) else "partially_refunded"
            _audit(db, case.id, "REFUND_SETTLED", "支付渠道确认退款成功。", "internal_service", provider, event_id)
        else:
            from .fulfillment import _create_incident
            _create_incident(db, case.id, "PAYMENT_REFUND_FAILED", "支付渠道返回退款失败，等待财务处理。", f"payment-failed:{intent.id}")
            _audit(db, case.id, "REFUND_FAILED", "支付渠道返回退款失败，等待运营处理。", "internal_service", provider, event_id)
    db.commit(); return intent

def reconcile_refunds(db: Session, provider: str, provider_statuses: dict[str, str]) -> PaymentReconciliationRun:
    run = PaymentReconciliationRun(id=str(uuid4()), provider=provider, status="completed", completed_at=datetime.now(timezone.utc)); db.add(run)
    for intent in db.scalars(select(RefundIntent).where(RefundIntent.provider == provider, RefundIntent.status.in_(("submitted", "succeeded")))):
        remote = provider_statuses.get(intent.provider_refund_id or "")
        status = "matched" if remote == intent.status else "missing_at_provider" if remote is None else "status_mismatch"
        db.add(PaymentReconciliationItem(id=str(uuid4()), run_id=run.id, refund_intent_id=intent.id, status=status, provider_status=remote, detail=None if status == "matched" else "渠道与本地退款状态不一致。"))
        if status != "matched":
            from .fulfillment import _create_incident
            case = db.get(AfterSalesCase, intent.case_id)
            if case is not None: _create_incident(db, case.id, "PAYMENT_RECONCILIATION_DIFF", f"退款对账差异：{status}", f"payment-reconcile:{run.id}:{intent.id}")
    db.commit(); return run

# Provider boundary: the worker only submits an intent; settlement always arrives
# through a separately verified callback or reconciliation read.
class PaymentProvider:
    def request_refund(self, payload: dict, idempotency_key: str) -> dict: raise NotImplementedError
    def get_refund_statuses(self) -> dict[str, str]: raise NotImplementedError

class SimulatorPaymentProvider(PaymentProvider):
    def __init__(self): self.statuses: dict[str, str] = {}
    def request_refund(self, payload: dict, idempotency_key: str) -> dict:
        provider_refund_id = f"demo-refund-{payload['intent_id']}"
        self.statuses.setdefault(provider_refund_id, "submitted")
        return {"provider_refund_id": provider_refund_id, "accepted": True}
    def get_refund_statuses(self) -> dict[str, str]: return dict(self.statuses)

def enqueue_refund_submission(db: Session, intent: RefundIntent) -> None:
    from .fulfillment import enqueue_outbox
    enqueue_outbox(db, aggregate_type="refund_intent", aggregate_id=intent.id, event_type="refund.requested", destination="payment_provider", payload={"intent_id": intent.id, "amount": str(intent.amount), "currency": intent.currency}, idempotency_key=intent.idempotency_key)

def submit_refund_provider(db: Session, intent_id: str, provider: PaymentProvider | None = None) -> RefundIntent:
    """Worker command; durable attempt is recorded before acknowledging delivery."""
    provider = provider or SimulatorPaymentProvider()
    intent = db.scalar(select(RefundIntent).where(RefundIntent.id == intent_id).with_for_update())
    if intent is None: raise DomainError("REFUND_INTENT_NOT_FOUND", "退款意图不存在。", 404)
    if intent.status == "succeeded": return intent
    response = provider.request_refund({"intent_id": intent.id, "amount": str(intent.amount), "currency": intent.currency}, intent.idempotency_key)
    if not response.get("accepted") or not response.get("provider_refund_id"):
        raise DomainError("PAYMENT_PROVIDER_REJECTED", "支付渠道拒绝退款提交。", 502)
    # same state/attempt semantics as the legacy call, with provider reference supplied by adapter.
    attempt = int(db.scalar(select(func.max(RefundAttempt.attempt_no)).where(RefundAttempt.refund_intent_id == intent.id)) or 0) + 1
    intent.status, intent.provider_refund_id, intent.failure_code = "submitted", str(response["provider_refund_id"]), None
    case = db.get(AfterSalesCase, intent.case_id); assert case is not None
    case.status = "refund_processing"
    db.add(RefundAttempt(id=str(uuid4()), refund_intent_id=intent.id, attempt_no=attempt, status="submitted", provider_response=response))
    _audit(db, case.id, "REFUND_SUBMITTED", "退款请求已提交支付渠道。", "internal_service", intent.provider, intent.id)
    db.commit(); return intent

def reconcile_provider(db: Session, provider_name: str, provider: PaymentProvider | None = None) -> PaymentReconciliationRun:
    provider = provider or SimulatorPaymentProvider()
    return reconcile_refunds(db, provider_name, provider.get_refund_statuses())

def retry_refund_submission(db: Session, intent_id: str, actor_id: str) -> RefundIntent:
    """Finance retry creates a new durable command; it never reuses a delivered outbox row."""
    from .fulfillment import enqueue_outbox
    intent = db.scalar(select(RefundIntent).where(RefundIntent.id == intent_id).with_for_update())
    if intent is None: raise DomainError("REFUND_INTENT_NOT_FOUND", "退款意图不存在。", 404)
    if intent.status not in {"failed", "pending"}: raise DomainError("REFUND_RETRY_NOT_ALLOWED", "当前退款状态不能重试。", 409)
    attempt = int(db.scalar(select(func.max(RefundAttempt.attempt_no)).where(RefundAttempt.refund_intent_id == intent.id)) or 0) + 1
    intent.status, intent.failure_code = "pending", None
    enqueue_outbox(db, aggregate_type="refund_intent", aggregate_id=intent.id, event_type="refund.requested", destination="payment_provider", payload={"intent_id":intent.id,"amount":str(intent.amount),"currency":intent.currency,"retry_attempt":attempt}, idempotency_key=f"{intent.idempotency_key}:retry:{attempt}")
    case=db.get(AfterSalesCase,intent.case_id); assert case is not None
    _audit(db,case.id,"REFUND_RETRY_REQUESTED",f"财务人员 {actor_id} 请求第 {attempt} 次退款投递。","finance",actor_id,intent.id)
    db.commit(); return intent
