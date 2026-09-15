from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from backend.app.database import Base
from backend.app.domain.payments import apply_refund_settlement, ensure_refund_intent, reconcile_refunds, submit_refund
from backend.app.domain.service import confirm_case, create_case, schedule_pickup
from backend.app.models import AfterSalesCase, PaymentProviderEvent, PaymentReconciliationItem, PaymentTransaction, RefundAttempt
from backend.app.schemas import AfterSalesCreateRequest
from backend.app.seed import seed_demo_data


def test_payment_callback_is_idempotent_settles_case_and_reconciliation_finds_gap(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'payments.db'}"); Base.metadata.create_all(engine); sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_demo_data(db)
        case = create_case(db, "U001", AfterSalesCreateRequest(order_id="O1001", request_type="refund", reason="退款"), "payment-case-create")
        confirm_case(db, "U001", case.id, "payment-case-confirm"); schedule_pickup(db, "U001", case.id, "明天上午", "payment-case-pickup")
        intent = ensure_refund_intent(db, case); db.commit(); submitted = submit_refund(db, intent.id)
        assert db.get(AfterSalesCase, case.id).status == "refund_processing"
        settled = apply_refund_settlement(db, "demo_payment", submitted.provider_refund_id, True, "pay-event-001", {"amount": "299.00"})
        replay = apply_refund_settlement(db, "demo_payment", submitted.provider_refund_id, True, "pay-event-001", {"amount": "299.00"})
        assert settled.id == replay.id == intent.id
        assert db.get(AfterSalesCase, case.id).status == "completed"
        assert db.get(PaymentTransaction, settled.payment_transaction_id).status == "refunded"
        assert len(list(db.scalars(select(PaymentProviderEvent)))) == 1
        assert len(list(db.scalars(select(RefundAttempt).where(RefundAttempt.refund_intent_id == intent.id)))) == 2
        run = reconcile_refunds(db, "demo_payment", {})
        item = db.scalar(select(PaymentReconciliationItem).where(PaymentReconciliationItem.run_id == run.id))
        assert item.status == "missing_at_provider"
