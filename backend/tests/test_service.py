from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.domain.service import (
    DomainError,
    cancel_case,
    complete_case,
    confirm_case,
    create_case,
    list_audit_logs,
    schedule_pickup,
)
from backend.app.models import AfterSalesCase, AuditLog
from backend.app.schemas import AfterSalesCreateRequest
from backend.app.seed import seed_demo_data


def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'service-test.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    seed_demo_data(session)
    return session


def refund_request(**overrides):
    payload = {"order_id": "O1001", "request_type": "refund", "reason": "商品未拆封，申请退款"}
    payload.update(overrides)
    return AfterSalesCreateRequest(**payload)


def assert_error(code, callback):
    try:
        callback()
    except DomainError as error:
        assert error.code == code
    else:
        raise AssertionError(f"应返回 {code}")


def test_creating_the_same_case_twice_is_idempotent_and_audited_once(tmp_path):
    db = db_session(tmp_path)
    first = create_case(db, "U001", refund_request(), "test-refund-o1001-v1")
    second = create_case(db, "U001", refund_request(), "test-refund-o1001-v1")
    assert first.id == second.id
    assert second.status == "pending_confirmation"
    assert db.scalar(select(func.count()).select_from(AfterSalesCase)) == 1
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 1


def test_same_idempotency_key_with_different_payload_is_rejected(tmp_path):
    db = db_session(tmp_path)
    create_case(db, "U001", refund_request(), "test-refund-o1001-v1")
    assert_error("IDEMPOTENCY_KEY_CONFLICT", lambda: create_case(db, "U001", refund_request(reason="改成另一种售后原因"), "test-refund-o1001-v1"))


def test_full_lifecycle_writes_an_auditable_state_chain(tmp_path):
    db = db_session(tmp_path)
    case = create_case(db, "U001", refund_request(), "create-lifecycle-v1", "trace-1")
    case = confirm_case(db, "U001", case.id, "confirm-lifecycle-v1", "trace-2")
    case = schedule_pickup(db, "U001", case.id, "明天上午", "pickup-lifecycle-v1", "trace-3")
    case = complete_case(db, case.id, "complete-lifecycle-v1", "trace-4")
    assert case.status == "completed"
    assert case.completed_at is not None
    events = list_audit_logs(db, "U001", case.id)
    assert [event.event_type for event in events] == ["CASE_CREATED", "CASE_CONFIRMED", "PICKUP_APPOINTMENT_RESERVED", "PICKUP_SCHEDULED", "CASE_COMPLETED"]
    assert events[-1].actor_id == "logistics-simulator"


def test_pickup_requires_confirmation_and_cross_user_is_denied(tmp_path):
    db = db_session(tmp_path)
    case = create_case(db, "U001", refund_request(), "create-guard-v1")
    assert_error("CASE_NOT_CONFIRMED", lambda: schedule_pickup(db, "U001", case.id, "明天上午", "pickup-guard-v1"))
    assert_error("CASE_ACCESS_DENIED", lambda: confirm_case(db, "U002", case.id, "confirm-guard-v1"))


def test_cancel_and_terminal_state_transition_guards(tmp_path):
    db = db_session(tmp_path)
    case = create_case(db, "U001", refund_request(), "create-cancel-v1")
    case = cancel_case(db, "U001", case.id, "cancel-v1")
    assert case.status == "cancelled"
    assert_error("INVALID_STATE_TRANSITION", lambda: confirm_case(db, "U001", case.id, "confirm-after-cancel-v1"))


def test_replaying_confirm_does_not_duplicate_audit_event(tmp_path):
    db = db_session(tmp_path)
    case = create_case(db, "U001", refund_request(), "create-replay-v1")
    confirm_case(db, "U001", case.id, "confirm-replay-v1")
    confirm_case(db, "U001", case.id, "confirm-replay-v1")
    assert [event.event_type for event in list_audit_logs(db, "U001", case.id)].count("CASE_CONFIRMED") == 1


def test_pickup_slot_rejects_past_ambiguous_and_out_of_range_values(tmp_path):
    db = db_session(tmp_path)
    case = create_case(db, "U001", refund_request(), "create-slot-validation-v1")
    confirm_case(db, "U001", case.id, "confirm-slot-validation-v1")
    assert_error("PICKUP_SLOT_IN_PAST", lambda: schedule_pickup(db, "U001", case.id, "昨天十二点", "slot-past-v1"))
    db.rollback()
    assert_error("PICKUP_SLOT_INVALID", lambda: schedule_pickup(db, "U001", case.id, "下午", "slot-ambiguous-v1"))
    db.rollback()
    assert_error("PICKUP_SLOT_OUT_OF_RANGE", lambda: schedule_pickup(db, "U001", case.id, "2099-01-01 上午", "slot-range-v1"))
    db.rollback()
    assert schedule_pickup(db, "U001", case.id, "明天上午", "slot-valid-v1").pickup_slot == "明天上午"
