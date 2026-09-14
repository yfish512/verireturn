from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from backend.app.database import Base
from backend.app.database import get_db
from backend.app.domain.knowledge import (
    EMBEDDING_DIMENSIONS,
    add_feedback,
    create_document,
    create_document_version,
    process_one_ingestion_job,
    publish_version,
    queue_ingestion,
    record_citations,
    retrieve,
)
from backend.app.domain.service import DomainError
from backend.app.models import KnowledgeDocumentVersion, KnowledgeIngestionJob, KnowledgeRetrievalLog
from backend.app.schemas import KnowledgeDocumentCreateRequest
from backend.app.seed import seed_demo_data
from backend.app.main import app


class DeterministicEmbeddings:
    model_name = "test-embedding-512"
    dimensions = EMBEDDING_DIMENSIONS

    def embed(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for character in text:
                vector[ord(character) % self.dimensions] += 1.0
            vectors.append(vector)
        return vectors


class FailingEmbeddings:
    model_name = "failing-embedding"
    dimensions = EMBEDDING_DIMENSIONS

    def embed(self, _texts):
        raise RuntimeError("temporary embedding failure")


def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'knowledge-test.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    seed_demo_data(session)
    return session


def create_and_publish(db, stable_key, audience, content, key):
    document, version = create_document(db, "OPS_MANAGER", KnowledgeDocumentCreateRequest(
        stable_key=stable_key, title=stable_key, audience=audience, category="after-sales", content_markdown=content,
    ), key)
    job = queue_ingestion(db, "OPS_MANAGER", version.id, f"{key}-queue")
    assert job.status == "queued"
    assert process_one_ingestion_job(db, DeterministicEmbeddings()) == job.id
    published = publish_version(db, "OPS_MANAGER", version.id, f"{key}-publish")
    assert published.status == "published"
    return document, published


def assert_error(code, callback):
    try:
        callback()
    except DomainError as error:
        assert error.code == code
    else:
        raise AssertionError(f"应返回 {code}")


def test_knowledge_lifecycle_hybrid_retrieval_permission_and_citations(tmp_path):
    db = db_session(tmp_path)
    public, public_version = create_and_publish(
        db, "refund-policy", "customer", "# 退款时效\n\n签收后七天内、商品未拆封可申请退款。上门取件后等待物流签收。", "knowledge-public-v1",
    )
    internal, _ = create_and_publish(
        db, "quality-review-sop", "operator", "# 内部审核 SOP\n\n质量争议退款必须核验图片和故障描述，不能向客户承诺审核通过。", "knowledge-internal-v1",
    )

    customer = retrieve(db, "U001", "customer", "签收后多久可以退款", provider=DeterministicEmbeddings())
    assert customer.hits
    assert {hit.document_id for hit in customer.hits} == {public.id}
    assert customer.hits[0].document_version_id == public_version.id
    record_citations(db, customer.retrieval_id, "U001", [customer.hits[0].chunk_id])
    log = db.get(KnowledgeRetrievalLog, customer.retrieval_id)
    assert log.cited_chunk_ids == [customer.hits[0].chunk_id]
    assert_error("KNOWLEDGE_CITATION_INVALID", lambda: record_citations(db, customer.retrieval_id, "U001", ["forged-chunk"]))
    feedback = add_feedback(db, customer.retrieval_id, "U001", "helpful", "说明清楚")
    assert feedback.rating == "helpful"
    assert_error("KNOWLEDGE_FEEDBACK_EXISTS", lambda: add_feedback(db, customer.retrieval_id, "U001", "helpful", None))

    operator = retrieve(db, "OPS001", "operator", "质量争议审核需要什么材料", provider=DeterministicEmbeddings())
    assert {hit.document_id for hit in operator.hits} == {internal.id}
    assert_error("KNOWLEDGE_RETRIEVAL_NOT_FOUND", lambda: add_feedback(db, operator.retrieval_id, "U001", "helpful", None))


def test_index_job_is_idempotent_and_published_version_is_replaced_by_new_version(tmp_path):
    db = db_session(tmp_path)
    document, old = create_and_publish(
        db, "pickup-policy", "shared", "# 取件流程\n\n客户确认售后单后可以预约取件时段。", "knowledge-pickup-v1",
    )
    # A replay must return the original version and never create a second job.
    replay = queue_ingestion(db, "OPS_MANAGER", old.id, "knowledge-pickup-v1-queue")
    assert replay.status == "succeeded"
    assert len(list(db.scalars(select(KnowledgeIngestionJob)))) == 1

    new = create_document_version(db, "OPS_MANAGER", document.id, "# 取件流程\n\n客户确认售后单后可预约上午、下午或晚上取件。", "knowledge-pickup-v2")
    job = queue_ingestion(db, "OPS_MANAGER", new.id, "knowledge-pickup-v2-queue")
    process_one_ingestion_job(db, DeterministicEmbeddings())
    published = publish_version(db, "OPS_MANAGER", new.id, "knowledge-pickup-v2-publish")
    assert published.status == "published"
    assert db.get(KnowledgeDocumentVersion, old.id).status == "retired"
    assert db.get(KnowledgeDocumentVersion, new.id).status == "published"
    assert db.get(KnowledgeIngestionJob, job.id).status == "succeeded"
    assert len(list(db.scalars(select(KnowledgeDocumentVersion).where(
        KnowledgeDocumentVersion.document_id == document.id,
        KnowledgeDocumentVersion.status == "published",
    )))) == 1


def test_customer_cannot_retrieve_only_operator_documents(tmp_path):
    db = db_session(tmp_path)
    create_and_publish(
        db, "operator-only", "operator", "# 风险 SOP\n\n拒绝原因必须按内部风险分类记录。", "knowledge-operator-only-v1",
    )
    result = retrieve(db, "U001", "customer", "内部风险分类怎么记录", provider=DeterministicEmbeddings())
    assert result.hits == []


def test_failed_ingestion_job_can_be_requeued_without_creating_a_duplicate(tmp_path):
    db = db_session(tmp_path)
    _, version = create_document(db, "OPS_MANAGER", KnowledgeDocumentCreateRequest(
        stable_key="retryable-worker", title="可重试任务", audience="customer", category="after-sales",
        content_markdown="# 可重试\n\n索引任务失败后应该能够保留任务身份并再次入队。",
    ), "knowledge-retry-create-v1")
    job = queue_ingestion(db, "OPS_MANAGER", version.id, "knowledge-retry-queue-v1")
    try:
        process_one_ingestion_job(db, FailingEmbeddings())
    except RuntimeError as error:
        assert "temporary" in str(error)
    else:
        raise AssertionError("失败 embedding 应传播错误给 worker supervisor")
    assert db.get(KnowledgeIngestionJob, job.id).status == "failed"
    requeued = queue_ingestion(db, "OPS_MANAGER", version.id, "knowledge-retry-queue-v2")
    assert requeued.id == job.id
    assert requeued.status == "queued"
    assert db.get(KnowledgeDocumentVersion, version.id).status == "indexing"


def test_knowledge_api_enforces_manager_write_and_customer_scope(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'knowledge-api-test.db'}", connect_args={"check_same_thread": False})
    sessions = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    with sessions() as db:
        seed_demo_data(db)

    def override_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        payload = {
            "stable_key": "api-public-policy", "title": "API 退款政策", "audience": "customer", "category": "after-sales",
            "content_markdown": "# 退款政策\n\n签收后七天内且商品未拆封，可以发起退款申请。",
        }
        denied = client.post("/ops/knowledge/documents", headers={"X-Demo-User-Id": "OPS001", "Idempotency-Key": "knowledge-api-denied-v1"}, json=payload)
        assert denied.status_code == 403
        created = client.post("/ops/knowledge/documents", headers={"X-Demo-User-Id": "OPS_MANAGER", "Idempotency-Key": "knowledge-api-create-v1"}, json=payload)
        assert created.status_code == 201
        version_id = created.json()["versions"][0]["id"]
        queued = client.post(f"/ops/knowledge/versions/{version_id}/ingestion", headers={"X-Demo-User-Id": "OPS_MANAGER", "Idempotency-Key": "knowledge-api-queue-v1"})
        assert queued.status_code == 202
        with sessions() as db:
            process_one_ingestion_job(db, DeterministicEmbeddings())
        published = client.post(f"/ops/knowledge/versions/{version_id}/publish", headers={"X-Demo-User-Id": "OPS_MANAGER", "Idempotency-Key": "knowledge-api-publish-v1"})
        assert published.status_code == 200
        answer = client.post("/knowledge/search", headers={"X-Demo-User-Id": "U001"}, json={"query": "退款时效", "limit": 3})
        assert answer.status_code == 200
        assert answer.json()["hits"][0]["title"] == "API 退款政策"
        other = client.post("/knowledge/search", headers={"X-Demo-User-Id": "U002"}, json={"query": "退款时效"})
        assert other.status_code == 200
        feedback = client.post(
            f"/knowledge/retrievals/{answer.json()['retrieval_id']}/feedback", headers={"X-Demo-User-Id": "U002"},
            json={"rating": "helpful"},
        )
        assert feedback.status_code == 404
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
