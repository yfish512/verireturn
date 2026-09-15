"""Minimal data-rights worker: export only owned records and redact mutable conversation content."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..models import AgentMessageRecord, AgentThread, AfterSalesCase, AuditLog, Order, PrivacyRequest, ReviewTicket

def export_customer_data(db: Session, user_id: str) -> dict:
    return {
        "user_id": user_id,
        "orders": [{"id":o.id,"item_name":o.item_name,"amount":str(o.amount),"status":o.status} for o in db.scalars(select(Order).where(Order.user_id==user_id))],
        "after_sales": [{"id":c.id,"order_id":c.order_id,"request_type":c.request_type,"status":c.status,"amount":str(c.eligible_amount)} for c in db.scalars(select(AfterSalesCase).where(AfterSalesCase.user_id==user_id))],
        "review_tickets": [{"id":t.id,"order_id":t.order_id,"status":t.status} for t in db.scalars(select(ReviewTicket).where(ReviewTicket.requester_id==user_id))],
        "messages": [{"thread_id":m.thread_id,"role":m.role,"content":m.content,"created_at":m.created_at.isoformat()} for m in db.scalars(select(AgentMessageRecord).join(AgentThread).where(AgentThread.actor_id==user_id))],
    }

def create_privacy_request(db: Session, user_id: str, request_type: str) -> PrivacyRequest:
    row=PrivacyRequest(id=str(uuid4()),user_id=user_id,request_type=request_type,status="processing"); db.add(row); db.flush()
    if request_type == "export": row.result_json=export_customer_data(db,user_id)
    else:
        # Financial and audit records must remain immutable; only customer-provided free text is redacted.
        threads=list(db.scalars(select(AgentThread).where(AgentThread.actor_id==user_id)))
        ids=[t.id for t in threads]
        if ids:
            for msg in db.scalars(select(AgentMessageRecord).where(AgentMessageRecord.thread_id.in_(ids))):
                msg.content="[已按用户请求删除]"; msg.payload_json={"redacted":True}
        for ticket in db.scalars(select(ReviewTicket).where(ReviewTicket.requester_id==user_id)):
            ticket.reason="[已按用户请求删除]"
        row.result_json={"redacted_threads":len(ids),"retained":"financial_and_audit_records"}
    row.status="completed"; row.completed_at=datetime.now(timezone.utc); db.commit(); return row

def purge_expired_messages(db: Session, retention_days: int) -> int:
    cutoff=datetime.now(timezone.utc)-timedelta(days=retention_days); count=0
    for msg in db.scalars(select(AgentMessageRecord).where(AgentMessageRecord.created_at < cutoff)):
        if msg.content != "[保留期已到，内容已删除]": msg.content="[保留期已到，内容已删除]"; msg.payload_json={"redacted":True}; count+=1
    db.commit(); return count
