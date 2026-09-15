from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.domain.payments import apply_refund_settlement, ensure_refund_intent, submit_refund
from backend.app.domain.review_service import (
    attach_review_evidence, create_review_ticket, create_review_upload, expire_overdue_review_tickets,
)
from backend.app.domain.service import DomainError, confirm_case, create_case, schedule_pickup
from backend.app.schemas import AfterSalesCreateRequest, ReviewTicketCreateRequest
from backend.app.seed import seed_demo_data
from backend.app.security import redact_text


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm10.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    seed_demo_data(db)
    return db


def test_review_upload_is_owned_hashed_and_sla_expiry_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_UPLOAD_DIR", str(tmp_path / "uploads"))
    db = _session(tmp_path)
    ticket = create_review_ticket(db, "U001", ReviewTicketCreateRequest(order_id="O1003", request_type="refund", reason="质量故障"), "m10-ticket-create")
    upload = create_review_upload(db, "U001", "proof.png", "image/png", "aGVsbG8=")
    assert (tmp_path / "uploads" / "U001" / upload.id).read_bytes() == b"hello"
    attachment = attach_review_evidence(db, ticket.id, "U001", upload.object_ref, upload.content_hash, "image/png", ticket.version)
    assert attachment.object_ref == upload.object_ref
    with pytest.raises(DomainError) as denied:
        attach_review_evidence(db, ticket.id, "U001", "uploads/U002/forged", upload.content_hash, "image/png", ticket.version + 1)
    assert denied.value.code == "ATTACHMENT_REFERENCE_DENIED"
    ticket.due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert expire_overdue_review_tickets(db) == 1
    assert expire_overdue_review_tickets(db) == 0
    assert db.get(type(ticket), ticket.id).status == "expired"


def test_payment_callback_requires_authoritative_amount_and_currency(tmp_path):
    db = _session(tmp_path)
    case = create_case(db, "U001", AfterSalesCreateRequest(order_id="O1001", request_type="refund", reason="退款"), "m10-payment-case")
    confirm_case(db, "U001", case.id, "m10-payment-confirm")
    schedule_pickup(db, "U001", case.id, "明天上午", "m10-payment-pickup")
    intent = ensure_refund_intent(db, case); db.commit(); intent = submit_refund(db, intent.id)
    with pytest.raises(DomainError) as mismatch:
        apply_refund_settlement(db, "demo_payment", intent.provider_refund_id, True, "m10-pay-event-1", {"amount":"1.00", "currency":"CNY", "occurred_at":"2026-09-15T00:00:00+00:00"})
    assert mismatch.value.code == "PAYMENT_AMOUNT_MISMATCH"
    with pytest.raises(DomainError) as currency_mismatch:
        apply_refund_settlement(db, "demo_payment", intent.provider_refund_id, True, "m10-pay-event-2", {"amount":"299.00", "currency":"USD", "occurred_at":"2026-09-15T00:00:00+00:00"})
    assert currency_mismatch.value.code == "PAYMENT_AMOUNT_MISMATCH"
    settled = apply_refund_settlement(db, "demo_payment", intent.provider_refund_id, True, "m10-pay-event-3", {"amount":"299.00", "currency":"CNY", "occurred_at":"2026-09-15T00:00:00+00:00"})
    assert settled.status == "succeeded" and settled.amount == Decimal("299.00")


def test_llm_projection_redacts_common_customer_pii():
    text = "手机号13812345678 邮箱 test@example.com 身份证110101199001011234"
    redacted = redact_text(text)
    assert "13812345678" not in redacted and "test@example.com" not in redacted and "110101199001011234" not in redacted


def test_multiline_agent_task_requires_a_server_validated_line_selection(tmp_path):
    from backend.app.agent.memory import TaskMemory
    from backend.app.agent.schemas import IntentDecision
    engine = create_engine(f"sqlite:///{tmp_path / 'agent-lines.db'}")
    Base.metadata.create_all(engine); sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_demo_data(db)
        # Add a second line to turn this into a genuine multi-line order.
        from backend.app.models import OrderItem
        db.add(OrderItem(id="item-o1001-cover", order_id="O1001", sku="AURORA-COVER", title="耳机保护套", quantity=2, unit_amount=Decimal("10.00")))
        db.commit()
    memory = TaskMemory(sessions)
    message_id, _ = memory.start_message("m10-lines", "U001", "帮我退 O1001", "m10-lines-start")
    resolved = memory.resolve("m10-lines", "U001", message_id, "帮我退 O1001", IntentDecision(intent="create_after_sales", order_id="O1001", request_type="refund"))
    memory.finish_execution("m10-lines", message_id, "run-lines", {"status":"completed", "last_error_code":"ORDER_ITEMS_REQUIRED", "response":"请选择"})
    selected_id, _ = memory.start_message("m10-lines", "U001", "选择商品 item-o1001-cover 数量 2", "m10-lines-select")
    selected = memory.resolve("m10-lines", "U001", selected_id, "选择商品 item-o1001-cover 数量 2", IntentDecision(intent="unknown"))
    assert selected["graph_input"]["items"] == [{"order_item_id":"item-o1001-cover", "quantity":2}]
