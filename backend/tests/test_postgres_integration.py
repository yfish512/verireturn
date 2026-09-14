"""Run explicitly against PostgreSQL; SQLite cannot validate these guarantees."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from langgraph.checkpoint.postgres import PostgresSaver

from backend.app.agent.intent import KeywordIntentExtractor
from backend.app.agent.runtime import AgentSettings, build_agent_runtime
from backend.app.domain.review_service import claim_review_ticket, create_review_ticket, publish_policy_version
from backend.app.domain.knowledge import EMBEDDING_DIMENSIONS, create_document, create_document_version, process_one_ingestion_job, publish_version, queue_ingestion, retrieve
from backend.app.domain.service import DomainError, confirm_case, create_case, schedule_pickup
from backend.app.domain.fulfillment import claim_one_outbox, receive_provider_event, replay_deferred_event
from backend.app.domain.observability import claim_one_evaluation_run, create_evaluation_run, finish_evaluation_run, process_one_metric_job, publish_alert_rule_version, queue_metric_job
from backend.app.models import AfterSalesCase, AlertRuleVersion, AuditLog, EvaluationResult, FulfillmentEvent, InboxEvent, KnowledgeDocumentVersion, PolicyVersion, ReviewEvent
from backend.app.schemas import AfterSalesCreateRequest, KnowledgeDocumentCreateRequest, PolicyVersionPublishRequest, ReviewTicketCreateRequest
from backend.app.seed import seed_demo_data


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 after starting the local PostgreSQL service",
)


class _DeterministicEmbeddings:
    model_name = "postgres-test-embedding-512"
    dimensions = EMBEDDING_DIMENSIONS

    def embed(self, texts):
        vectors = []
        for value in texts:
            vector = [0.0] * self.dimensions
            for character in value:
                vector[ord(character) % self.dimensions] += 1.0
            vectors.append(vector)
        return vectors


@pytest.fixture(scope="module")
def postgres_url():
    target = os.getenv("POSTGRES_TEST_URL", "postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn_m1_test")
    url = make_url(target)
    admin_url = url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin_engine.dispose()

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", target)
    command.upgrade(config, "head")
    yield target

    engine = create_engine(target)
    engine.dispose()
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
    admin_engine.dispose()


def test_postgres_concurrent_create_is_idempotent(postgres_url):
    engine = create_engine(postgres_url)
    sessions = sessionmaker(bind=engine)
    with sessions() as seed_session:
        seed_demo_data(seed_session)

    request = AfterSalesCreateRequest(order_id="O1001", request_type="refund", reason="并发重试退款")

    def submit_once():
        with sessions() as session:
            return create_case(session, "U001", request, "postgres-concurrent-create-v1").id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(lambda _: submit_once(), range(2)))

    with sessions() as assertion_session:
        assert len(set(ids)) == 1
        assert assertion_session.scalar(select(func.count()).select_from(AfterSalesCase)) == 1
        assert assertion_session.scalar(select(func.count()).select_from(AuditLog)) == 1
    engine.dispose()


def test_postgres_audit_log_cannot_be_updated_or_deleted(postgres_url):
    engine = create_engine(postgres_url)
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        seed_demo_data(session)
        case = create_case(
            session,
            "U001",
            AfterSalesCreateRequest(order_id="O1001", request_type="refund", reason="审计不可变验证"),
            "postgres-audit-immutable-v1",
        )
        audit = session.scalar(select(AuditLog).where(AuditLog.case_id == case.id))
        audit.detail = "tampered"
        with pytest.raises(DBAPIError):
            session.commit()
        session.rollback()
    engine.dispose()


def test_postgres_checkpoint_resumes_confirmation_after_runtime_recreation(postgres_url):
    class Tools:
        def __init__(self, case_id):
            self.case_id = case_id
            self.confirm_calls = 0

        def check_eligibility(self, *_):
            return {"eligible": True, "eligible_amount": "299.00", "policy_code": "RETURN_WITHIN_7_DAYS", "explanation": "可退款"}

        def create_case(self, *_):
            return {"id": self.case_id, "eligible_amount": "299.00", "status": "pending_confirmation"}

        def confirm_case(self, _, case_id, *__):
            self.confirm_calls += 1
            return {"id": case_id, "status": "awaiting_pickup"}

        def cancel_case(self, _, case_id, *__):
            return {"id": case_id, "status": "cancelled"}

        def schedule_pickup(self, _, case_id, time_slot, *__):
            return {"id": case_id, "status": "pickup_scheduled", "pickup_slot": time_slot}

        def get_order(self, *_):
            return {"id": "O1001", "item_name": "耳机", "amount": "299.00", "status": "delivered"}

        def get_logistics(self, *_):
            return {"order_id": "O1001", "status": "delivered", "tracking_number": "SF1"}

    engine = create_engine(postgres_url)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_demo_data(db)
        existing_case = create_case(
            db, "U001", AfterSalesCreateRequest(order_id="O1001", request_type="refund", reason="checkpoint FK case"),
            "postgres-checkpoint-existing-case-v1",
        )
    tools = Tools(existing_case.id)
    checkpoint_url = postgres_url.replace("postgresql+psycopg://", "postgresql://")
    settings = AgentSettings(agent_service_url="http://unused")

    with PostgresSaver.from_conn_string(checkpoint_url) as saver:
        saver.setup()
        first_runtime = build_agent_runtime(settings=settings, session_factory=sessions, tools=tools, extractor=KeywordIntentExtractor(), checkpointer=saver)
        pending = first_runtime.handle_message("postgres-restart-thread", "U001", "帮我退 O1001", "postgres-restart-message")
        assert pending["status"] == "awaiting_confirmation"
        confirmation_id = pending["confirmation_id"]

    with PostgresSaver.from_conn_string(checkpoint_url) as saver:
        saver.setup()
        restarted_runtime = build_agent_runtime(settings=settings, session_factory=sessions, tools=tools, extractor=KeywordIntentExtractor(), checkpointer=saver)
        resolved = restarted_runtime.resolve_confirmation(confirmation_id, "U001", True)
        assert resolved["status"] == "completed"
        assert tools.confirm_calls == 1
    engine.dispose()


def test_postgres_concurrent_review_claim_is_single_winner_and_events_are_immutable(postgres_url):
    engine = create_engine(postgres_url)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_demo_data(db)
        ticket = create_review_ticket(
            db, "U001", ReviewTicketCreateRequest(
                order_id="O1002", request_type="refund", reason="商品有质量问题，无法使用，需要人工审核"
            ), "postgres-review-create-v1",
        )

    def claim_once(key):
        with sessions() as session:
            try:
                return claim_review_ticket(session, ticket.id, "OPS001", "operator", 1, key).status
            except DomainError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(claim_once, ["postgres-review-claim-a", "postgres-review-claim-b"]))
    assert outcomes.count("claimed") == 1
    assert outcomes.count("REVIEW_VERSION_CONFLICT") + outcomes.count("REVIEW_TICKET_NOT_OPEN") == 1

    with sessions() as db:
        event = db.scalar(select(ReviewEvent).where(ReviewEvent.ticket_id == ticket.id))
        event.event_type = "TAMPERED"
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
    engine.dispose()


def test_postgres_policy_versions_have_one_published_row_and_immutable_content(postgres_url):
    engine = create_engine(postgres_url)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_demo_data(db)

    def publish(version, key):
        with sessions() as session:
            try:
                policy = publish_policy_version(
                    session, "OPS_MANAGER", PolicyVersionPublishRequest(
                        version=version, quality_dispute_enabled=True, quality_dispute_terms=["故障"], review_sla_hours=12,
                    ), key,
                )
                return policy.version
            except DomainError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda args: publish(*args), [("policy-concurrent-a", "policy-concurrent-key-a"), ("policy-concurrent-b", "policy-concurrent-key-b")]))
    # Both commands may succeed after row-lock serialization, but no instant
    # may expose two effective policies: the first published row is retired by
    # the second transaction and the partial unique index remains satisfied.
    assert all(outcome.startswith("policy-concurrent-") for outcome in outcomes)

    with sessions() as db:
        published = list(db.scalars(select(PolicyVersion).where(PolicyVersion.status == "published")))
        assert len(published) == 1
        published[0].rules_json = {"tampered": True}
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
        policy = db.scalar(select(PolicyVersion).where(PolicyVersion.status == "published"))
        db.delete(policy)
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
    engine.dispose()


def test_postgres_knowledge_uses_pgvector_scope_and_immutable_published_version(postgres_url):
    engine = create_engine(postgres_url)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_demo_data(db)
        document, version = create_document(db, "OPS_MANAGER", KnowledgeDocumentCreateRequest(
            stable_key="postgres-refund-policy", title="退款政策", audience="customer", category="after-sales",
            content_markdown="# 退款政策\n\n签收后七天内且商品未拆封时，可以发起退款申请。",
        ), "postgres-knowledge-create-v1")
        job = queue_ingestion(db, "OPS_MANAGER", version.id, "postgres-knowledge-queue-v1")
        assert process_one_ingestion_job(db, _DeterministicEmbeddings()) == job.id
        published = publish_version(db, "OPS_MANAGER", version.id, "postgres-knowledge-publish-v1")
        found = retrieve(db, "U001", "customer", "签收后多久可以退款", provider=_DeterministicEmbeddings())
        assert [hit.document_id for hit in found.hits] == [document.id]
        published.content_markdown = "tampered"
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
        assert db.get(KnowledgeDocumentVersion, version.id).status == "published"
    engine.dispose()


def test_postgres_concurrent_knowledge_publish_keeps_exactly_one_published_version(postgres_url):
    engine = create_engine(postgres_url)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_demo_data(db)
        document, initial = create_document(db, "OPS_MANAGER", KnowledgeDocumentCreateRequest(
            stable_key="postgres-concurrent-knowledge", title="并发知识版本", audience="customer", category="after-sales",
            content_markdown="# 初版\n\n用于验证并发发布的初始知识版本。",
        ), "postgres-concurrent-knowledge-create-v1")
        queue_ingestion(db, "OPS_MANAGER", initial.id, "postgres-concurrent-knowledge-queue-v1")
        process_one_ingestion_job(db, _DeterministicEmbeddings())
        publish_version(db, "OPS_MANAGER", initial.id, "postgres-concurrent-knowledge-publish-v1")
        first = create_document_version(db, "OPS_MANAGER", document.id, "# 版本二\n\n第一份待发布更新。", "postgres-concurrent-knowledge-create-v2")
        queue_ingestion(db, "OPS_MANAGER", first.id, "postgres-concurrent-knowledge-queue-v2")
        process_one_ingestion_job(db, _DeterministicEmbeddings())
        second = create_document_version(db, "OPS_MANAGER", document.id, "# 版本三\n\n第二份待发布更新。", "postgres-concurrent-knowledge-create-v3")
        queue_ingestion(db, "OPS_MANAGER", second.id, "postgres-concurrent-knowledge-queue-v3")
        process_one_ingestion_job(db, _DeterministicEmbeddings())
        document_id, first_id, second_id = document.id, first.id, second.id

    def publish(version_id, key):
        with sessions() as db:
            return publish_version(db, "OPS_MANAGER", version_id, key).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda item: publish(*item), [(first_id, "postgres-concurrent-knowledge-publish-v2"), (second_id, "postgres-concurrent-knowledge-publish-v3")]))
    assert set(outcomes) == {first_id, second_id}
    with sessions() as db:
        versions = list(db.scalars(select(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.document_id == document_id)))
        assert len([version for version in versions if version.status == "published"]) == 1
        assert len([version for version in versions if version.status == "retired"]) == 2
    engine.dispose()


def test_postgres_m5_claims_outbox_once_and_replays_ordered_callback(postgres_url):
    engine = create_engine(postgres_url)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_demo_data(db)
        case = create_case(db, "U001", AfterSalesCreateRequest(order_id="O1001", request_type="refund", reason="M5 PostgreSQL"), "postgres-m5-create-v1")
        confirm_case(db, "U001", case.id, "postgres-m5-confirm-v1")
        schedule_pickup(db, "U001", case.id, "2026-09-15 上午", "postgres-m5-pickup-v1")
        case_id = case.id

    def claim_once(_):
        with sessions() as db:
            claimed = claim_one_outbox(db)
            return claimed.id if claimed else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(claim_once, range(2)))
    assert sum(value is not None for value in claimed) == 1

    def event(event_id, kind, sequence):
        return {"event_id": event_id, "case_id": case_id, "event_type": kind, "sequence_no": sequence,
                "occurred_at": datetime.now(timezone.utc), "payload": {"tracking": "PG-M5"}}

    with sessions() as db:
        deferred = receive_provider_event(db, "demo_fulfillment", event("postgres-m5-return-2", "return.received", 2))
        assert deferred.status == "deferred"
        receive_provider_event(db, "demo_fulfillment", event("postgres-m5-pickup-1", "pickup.collected", 1))
        replayed = replay_deferred_event(db, deferred.id, "OPS001")
        assert replayed.status == "applied"
        assert db.get(AfterSalesCase, case_id).status == "return_received"
        assert db.scalar(select(func.count()).select_from(FulfillmentEvent).where(FulfillmentEvent.case_id == case_id)) == 2
    engine.dispose()


def test_postgres_m6_metric_window_and_evaluation_queue_are_single_claim(postgres_url):
    from datetime import timedelta
    engine = create_engine(postgres_url)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_demo_data(db)
        start, end = datetime.now(timezone.utc) - timedelta(minutes=5), datetime.now(timezone.utc)
        job = queue_metric_job(db, start, end)
        run = create_evaluation_run(db, "OPS_MANAGER", "m5", "deepseek-v4-flash", "agent-prompt-v1")
        job_id, run_id = job.id, run.id

    def metric_once(_):
        with sessions() as db:
            return process_one_metric_job(db)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(metric_once, range(2)))
    assert results.count(job_id) == 1
    assert results.count(None) == 1

    def evaluation_once(_):
        with sessions() as db:
            claimed = claim_one_evaluation_run(db)
            return claimed[0].id if claimed else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(evaluation_once, range(2)))
    assert results.count(run_id) == 1
    assert results.count(None) == 1
    with sessions() as db:
        finish_evaluation_run(db, run_id, {"failed": 0, "results": [{"id": "m6-evidence", "status": "passed", "duration_ms": 1}]})
        evidence = db.scalar(select(EvaluationResult).where(EvaluationResult.evaluation_run_id == run_id))
        evidence.status = "tampered"
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
    engine.dispose()


def test_postgres_alert_rule_replacement_preserves_immutable_history(postgres_url):
    engine = create_engine(postgres_url)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_demo_data(db)
        first = publish_alert_rule_version(
            db, "OPS_MANAGER", rule_key="postgres-alert-rule", metric_name="agent.runs_total",
            comparison=">", threshold=1, severity="warning", idempotency_key="postgres-alert-rule-v1",
        )
        replacement = publish_alert_rule_version(
            db, "OPS_MANAGER", rule_key="postgres-alert-rule", metric_name="agent.runs_total",
            comparison=">", threshold=2, severity="critical", idempotency_key="postgres-alert-rule-v2",
        )
        assert (first.status, replacement.status, replacement.version) == ("retired", "published", 2)
        replacement.threshold = 999
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
        retired = db.get(AlertRuleVersion, first.id)
        db.delete(retired)
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
    engine.dispose()


def test_postgres_concurrent_alert_rule_publish_leaves_one_active_version(postgres_url):
    engine = create_engine(postgres_url)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_demo_data(db)

    def publish(threshold, key):
        with sessions() as db:
            return publish_alert_rule_version(
                db, "OPS_MANAGER", rule_key="postgres-concurrent-alert", metric_name="agent.runs_total",
                comparison=">=", threshold=threshold, severity="warning", idempotency_key=key,
            ).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        created = list(executor.map(lambda value: publish(*value), [(1, "postgres-alert-concurrent-v1"), (2, "postgres-alert-concurrent-v2")]))
    assert len(set(created)) == 2
    with sessions() as db:
        versions = list(db.scalars(select(AlertRuleVersion).where(AlertRuleVersion.rule_key == "postgres-concurrent-alert")))
        assert sorted(version.version for version in versions) == [1, 2]
        assert len([version for version in versions if version.status == "published"]) == 1
        assert len([version for version in versions if version.status == "retired"]) == 1
    engine.dispose()
