from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.domain.fulfillment import (
    acknowledge_incident, process_one_outbox, receive_provider_event, replay_deferred_event, resolve_incident,
)
from backend.app.domain.service import confirm_case, create_case, schedule_pickup
from backend.app.models import AuditLog, CustomerNotification, FulfillmentEvent, FulfillmentIncident, InboxEvent, OutboxDelivery, OutboxEvent
from backend.app.schemas import AfterSalesCreateRequest
from backend.app.seed import seed_demo_data


def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fulfillment-test.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    seed_demo_data(session)
    return session


def ready_case(db, request_type="refund"):
    order_id = "O1001" if request_type == "refund" else "O1003"
    case = create_case(db, "U001", AfterSalesCreateRequest(order_id=order_id, request_type=request_type, reason="M5 履约测试"), f"m5-create-{request_type}")
    confirm_case(db, "U001", case.id, f"m5-confirm-{request_type}")
    return schedule_pickup(db, "U001", case.id, "明天上午", f"m5-pickup-{request_type}")


def callback(case_id, event_id, event_type, sequence_no):
    return {
        "event_id": event_id, "case_id": case_id, "event_type": event_type, "sequence_no": sequence_no,
        "occurred_at": datetime.now(timezone.utc), "payload": {"provider_tracking": "M5-DEMO-001"},
    }


def test_pickup_transition_writes_outbox_atomically_and_worker_delivers(tmp_path):
    db = db_session(tmp_path)
    case = ready_case(db)
    outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == str(case.id)))
    assert outbox.event_type == "pickup.requested"
    assert outbox.status == "pending"
    assert process_one_outbox(db) == outbox.id
    assert db.get(OutboxEvent, outbox.id).status == "delivered"
    delivery = db.scalar(select(OutboxDelivery).where(OutboxDelivery.outbox_event_id == outbox.id))
    assert delivery.status == "succeeded"


def test_refund_callbacks_are_sequenced_and_idempotent(tmp_path):
    db = db_session(tmp_path)
    case = ready_case(db)
    callbacks = [
        ("event-pickup-0001", "pickup.collected", 1), ("event-return-0002", "return.received", 2),
        ("event-refund-0003", "refund.processing", 3), ("event-refund-0004", "refund.completed", 4),
    ]
    delivered = [callback(case.id, *item) for item in callbacks]
    for payload in delivered:
        receive_provider_event(db, "demo_fulfillment", payload)
    assert db.get(type(case), case.id).status == "completed"
    assert db.scalar(select(func.count()).select_from(FulfillmentEvent).where(FulfillmentEvent.case_id == case.id)) == 4
    duplicate = receive_provider_event(db, "demo_fulfillment", delivered[-1])
    assert duplicate.status == "applied"
    assert db.scalar(select(func.count()).select_from(FulfillmentEvent).where(FulfillmentEvent.case_id == case.id)) == 4
    assert db.scalar(select(func.count()).select_from(CustomerNotification).where(CustomerNotification.case_id == case.id)) == 4


def test_out_of_order_event_is_deferred_then_operator_replay_applies(tmp_path):
    db = db_session(tmp_path)
    case = ready_case(db)
    delayed = receive_provider_event(db, "demo_fulfillment", callback(case.id, "event-return-0012", "return.received", 2))
    assert delayed.status == "deferred"
    receive_provider_event(db, "demo_fulfillment", callback(case.id, "event-pickup-0011", "pickup.collected", 1))
    replayed = replay_deferred_event(db, delayed.id, "OPS001")
    assert replayed.status == "applied"
    assert db.get(type(case), case.id).status == "return_received"
    assert db.scalar(select(func.count()).select_from(FulfillmentIncident).where(FulfillmentIncident.case_id == case.id)) == 1


def test_wrong_type_is_rejected_and_creates_incident_without_state_change(tmp_path):
    db = db_session(tmp_path)
    case = ready_case(db, "exchange")
    receive_provider_event(db, "demo_fulfillment", callback(case.id, "event-exchange-0001", "pickup.collected", 1))
    receive_provider_event(db, "demo_fulfillment", callback(case.id, "event-exchange-0002", "return.received", 2))
    rejected = receive_provider_event(db, "demo_fulfillment", callback(case.id, "event-exchange-0003", "refund.processing", 3))
    assert rejected.status == "rejected"
    assert db.get(type(case), case.id).status == "return_received"
    assert db.scalar(select(FulfillmentIncident).where(FulfillmentIncident.inbox_event_id == rejected.id)).incident_type == "INVALID_FULFILLMENT_TRANSITION"


def test_outbox_failure_is_recorded_and_retry_can_recover(tmp_path):
    class FailingProvider:
        def request_pickup(self, payload, idempotency_key):
            raise RuntimeError("temporary provider failure")

    db = db_session(tmp_path)
    case = ready_case(db)
    outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == str(case.id)))
    process_one_outbox(db, provider=FailingProvider())
    failed = db.get(OutboxEvent, outbox.id)
    assert failed.status == "pending"
    assert db.scalar(select(OutboxDelivery).where(OutboxDelivery.outbox_event_id == outbox.id)).status == "failed"
    # A worker normally waits for next_attempt_at; set it due in a deterministic unit test.
    failed.next_attempt_at = datetime.now(timezone.utc)
    db.commit()
    process_one_outbox(db)
    assert db.get(OutboxEvent, outbox.id).status == "delivered"


def test_operator_incident_actions_are_versioned_and_audited(tmp_path):
    db = db_session(tmp_path)
    case = ready_case(db)
    receive_provider_event(db, "demo_fulfillment", callback(case.id, "event-ops-0002", "return.received", 2))
    incident = db.scalar(select(FulfillmentIncident).where(FulfillmentIncident.case_id == case.id))
    acknowledged = acknowledge_incident(db, incident.id, "OPS001", incident.version)
    assert acknowledged.status == "acknowledged"
    resolved = resolve_incident(db, incident.id, "OPS001", acknowledged.version, "等待前序回调后已重放")
    assert resolved.status == "resolved"
    assert [log.event_type for log in db.scalars(select(AuditLog).where(AuditLog.case_id == case.id).order_by(AuditLog.id))][-2:] == [
        "FULFILLMENT_INCIDENT_ACKNOWLEDGED", "FULFILLMENT_INCIDENT_RESOLVED"
    ]
