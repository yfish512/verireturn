from datetime import datetime, timezone
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.domain.payments import apply_refund_settlement, ensure_refund_intent, submit_refund
from backend.app.domain.service import confirm_case, create_case, schedule_pickup
from backend.app.domain.fulfillment import receive_provider_event
from backend.app.models import PaymentTransaction, RefundAttempt, RefundIntent
from backend.app.schemas import AfterSalesCreateRequest
from backend.app.seed import seed_demo_data


def test_refund_intent_is_idempotent_and_settles_only_from_payment_fact(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'payments.db'}")
    Base.metadata.create_all(engine); sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_demo_data(db)
        case = create_case(db, "U001", AfterSalesCreateRequest(order_id="O1001", request_type="refund", reason="退款"), "payment-case-create")
        confirm_case(db, "U001", case.id, "payment-case-confirm")
        schedule_pickup(db, "U001", case.id, "明天上午", "payment-case-pickup")
        intent = ensure_refund_intent(db, case); same = ensure_refund_intent(db, case)
        assert intent.id == same.id
        db.commit()
        submitted = submit_refund(db, intent.id)
        settled = apply_refund_settlement(db, "demo_payment", submitted.provider_refund_id, True, "pay-event-001")
        assert settled.status == "succeeded"
        assert db.get(PaymentTransaction, settled.payment_transaction_id).status == "captured"
        assert len(list(db.scalars(select(RefundAttempt).where(RefundAttempt.refund_intent_id == intent.id)))) == 2
