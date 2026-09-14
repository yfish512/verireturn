from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.domain.review_service import claim_review_ticket, create_review_ticket, decide_review_ticket, list_review_events, supplement_review_ticket
from backend.app.domain.service import DomainError, confirm_case
from backend.app.models import AfterSalesCase, ReviewEvent
from backend.app.schemas import ReviewDecisionRequest, ReviewTicketCreateRequest
from backend.app.seed import seed_demo_data


def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'review-test.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    seed_demo_data(session)
    return session


def review_request(**overrides):
    payload = {"order_id": "O1002", "request_type": "refund", "reason": "商品有质量问题，无法使用，需要人工核验"}
    payload.update(overrides)
    return ReviewTicketCreateRequest(**payload)


def assert_error(code, callback):
    try:
        callback()
    except DomainError as error:
        assert error.code == code
    else:
        raise AssertionError(f"应返回 {code}")


def test_review_approval_creates_one_new_customer_confirmable_case(tmp_path):
    db = db_session(tmp_path)
    ticket = create_review_ticket(db, "U001", review_request(), "review-create-quality-v1", "request-create")
    assert ticket.status == "open"
    assert ticket.trigger_code == "QUALITY_DISPUTE_REQUIRES_EVIDENCE"

    ticket = claim_review_ticket(db, ticket.id, "OPS001", "operator", ticket.version, "review-claim-quality-v1", "request-claim")
    assert ticket.status == "claimed"
    decision = ReviewDecisionRequest(
        action="approve_exception", expected_version=ticket.version, reason_code="EVIDENCE_ACCEPTED", customer_message="已核验材料，请确认售后方案。"
    )
    approved = decide_review_ticket(db, ticket.id, "OPS001", "operator", decision, "review-approve-quality-v1", "request-approve")
    assert approved.status == "approved"
    case = db.scalar(select(AfterSalesCase).where(AfterSalesCase.source_review_ticket_id == ticket.id))
    assert case is not None
    assert case.status == "pending_confirmation"
    assert case.policy_version_id == ticket.policy_version_id
    confirm_case(db, "U001", case.id, "review-case-customer-confirm-v1")
    assert db.get(AfterSalesCase, case.id).status == "awaiting_pickup"
    replay = decide_review_ticket(db, ticket.id, "OPS001", "operator", decision, "review-approve-quality-v1", "request-approve")
    assert replay.id == ticket.id
    assert db.scalar(select(func.count()).select_from(AfterSalesCase).where(AfterSalesCase.source_review_ticket_id == ticket.id)) == 1
    assert [event.event_type for event in list_review_events(db, ticket.id)] == [
        "REVIEW_TICKET_CREATED", "REVIEW_TICKET_CLAIMED", "REVIEW_EXCEPTION_APPROVED"
    ]


def test_review_version_owner_and_customer_supplement_guards(tmp_path):
    db = db_session(tmp_path)
    ticket = create_review_ticket(db, "U001", review_request(), "review-create-guard-v1")
    assert_error("REVIEW_VERSION_CONFLICT", lambda: claim_review_ticket(db, ticket.id, "OPS001", "operator", 99, "review-claim-wrong-v1"))
    claimed = claim_review_ticket(db, ticket.id, "OPS001", "operator", ticket.version, "review-claim-guard-v1")
    request_info = ReviewDecisionRequest(
        action="request_more_info", expected_version=claimed.version, reason_code="NEED_PHOTO", customer_message="请补充故障描述。"
    )
    waiting = decide_review_ticket(db, ticket.id, "OPS001", "operator", request_info, "review-more-info-v1")
    assert waiting.status == "waiting_customer"
    assert_error("REVIEW_TICKET_ACCESS_DENIED", lambda: supplement_review_ticket(
        db, ticket.id, "U002", "伪造材料", waiting.version, "review-other-supplement-v1"
    ))
    resumed = supplement_review_ticket(db, ticket.id, "U001", "设备无法充电", waiting.version, "review-supplement-v1")
    assert resumed.status == "claimed"
    assert db.scalar(select(func.count()).select_from(ReviewEvent).where(ReviewEvent.ticket_id == ticket.id)) == 4


def test_automatic_or_hard_rejected_requests_cannot_be_placed_in_review_queue(tmp_path):
    db = db_session(tmp_path)
    assert_error("AUTO_PROCESS_AVAILABLE", lambda: create_review_ticket(
        db, "U001", review_request(order_id="O1001", reason="商品未拆封，申请退款"), "review-auto-v1"
    ))
    assert_error("REVIEW_NOT_ALLOWED", lambda: create_review_ticket(
        db, "U001", review_request(reason="不想要了"), "review-hard-reject-v1"
    ))
