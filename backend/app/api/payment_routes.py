from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..auth import ActorContext, require_finance, verify_payment_webhook
from ..database import get_db
from ..domain.payments import apply_refund_settlement, reconcile_provider, retry_refund_submission
from ..domain.service import DomainError
from ..models import PaymentProviderEvent, PaymentReconciliationItem, PaymentReconciliationRun, RefundIntent

payment_router = APIRouter(tags=["payments"])
def error(e: DomainError): return HTTPException(status_code=e.status_code, detail={"code":e.code,"message":e.message})

class PaymentWebhook(BaseModel):
    event_id: str = Field(min_length=8, max_length=128)
    provider_refund_id: str = Field(min_length=8, max_length=128)
    outcome: str = Field(pattern="^(succeeded|failed)$")
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    currency: str = Field(default="CNY", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    occurred_at: datetime
    payload: dict = Field(default_factory=dict)
    model_config={"extra":"forbid"}

class ReconcileRequest(BaseModel):
    expected_provider: str = Field(default="demo_payment", pattern="^[a-zA-Z0-9_.-]+$")
    model_config={"extra":"forbid"}

@payment_router.post("/internal/payments/webhooks/{provider}")
async def payment_webhook(provider: str, request: Request, signature: str = Header(alias="X-Provider-Signature", min_length=8), timestamp: str = Header(alias="X-Provider-Timestamp", min_length=10), event_id: str = Header(alias="X-Provider-Event-Id", min_length=8), db: Session = Depends(get_db)):
    if provider != "demo_payment": raise HTTPException(404, detail={"code":"PAYMENT_PROVIDER_NOT_FOUND","message":"未知支付渠道。"})
    raw = await request.body(); verify_payment_webhook(raw, signature, timestamp)
    try: body = PaymentWebhook.model_validate_json(raw)
    except ValidationError as exc: raise HTTPException(422, detail={"code":"PAYMENT_EVENT_INVALID","message":"支付回调内容无效。"}) from exc
    if body.event_id != event_id: raise HTTPException(422, detail={"code":"PAYMENT_EVENT_ID_MISMATCH","message":"回调头与正文事件 ID 不一致。"})
    intent = db.scalar(select(RefundIntent).where(RefundIntent.provider == provider, RefundIntent.provider_refund_id == body.provider_refund_id))
    if intent is None: raise HTTPException(404, detail={"code":"REFUND_PROVIDER_REFERENCE_NOT_FOUND","message":"支付回调未关联退款意图。"})
    if body.amount != intent.amount or body.currency != intent.currency: raise HTTPException(422, detail={"code":"PAYMENT_AMOUNT_MISMATCH","message":"支付回调金额或币种与退款意图不一致。"})
    try: return _intent_dict(apply_refund_settlement(db, provider, body.provider_refund_id, body.outcome == "succeeded", body.event_id, {**body.payload, "amount": str(body.amount), "currency": body.currency, "occurred_at": body.occurred_at.isoformat()}))
    except DomainError as exc: raise error(exc) from exc

@payment_router.get("/ops/payments/refunds")
def list_refunds(_: ActorContext = Depends(require_finance), db: Session = Depends(get_db)):
    return [_intent_dict(item) for item in db.scalars(select(RefundIntent).order_by(RefundIntent.created_at.desc()))]

@payment_router.post("/ops/payments/refunds/{intent_id}/retry")
def retry_refund(intent_id: str, actor: ActorContext = Depends(require_finance), db: Session = Depends(get_db)):
    try: return _intent_dict(retry_refund_submission(db, intent_id, actor.id))
    except DomainError as exc: raise error(exc) from exc


@payment_router.post("/ops/payments/reconciliation")
def run_reconciliation(body: ReconcileRequest, _: ActorContext = Depends(require_finance), db: Session = Depends(get_db)):
    run = reconcile_provider(db, body.expected_provider)
    items = list(db.scalars(select(PaymentReconciliationItem).where(PaymentReconciliationItem.run_id == run.id)))
    return {"id":run.id,"provider":run.provider,"status":run.status,"items":[{"id":i.id,"refund_intent_id":i.refund_intent_id,"status":i.status,"provider_status":i.provider_status,"detail":i.detail} for i in items]}

@payment_router.get("/ops/payments/reconciliation/{run_id}")
def get_reconciliation(run_id: str, _: ActorContext = Depends(require_finance), db: Session = Depends(get_db)):
    run=db.get(PaymentReconciliationRun,run_id)
    if not run: raise HTTPException(404, detail={"code":"RECONCILIATION_NOT_FOUND","message":"对账批次不存在。"})
    return {"id":run.id,"provider":run.provider,"status":run.status,"items":[{"id":i.id,"refund_intent_id":i.refund_intent_id,"status":i.status,"provider_status":i.provider_status,"detail":i.detail} for i in db.scalars(select(PaymentReconciliationItem).where(PaymentReconciliationItem.run_id==run.id))]}

def _intent_dict(i: RefundIntent): return {"id":i.id,"case_id":i.case_id,"amount":i.amount,"currency":i.currency,"provider":i.provider,"provider_refund_id":i.provider_refund_id,"status":i.status,"failure_code":i.failure_code,"created_at":i.created_at,"settled_at":i.settled_at}
