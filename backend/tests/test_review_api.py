from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base, get_db
from backend.app.main import app
from backend.app.seed import seed_demo_data


def test_review_api_enforces_server_side_roles_and_customer_ownership(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'review-api-test.db'}", connect_args={"check_same_thread": False})
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
    headers = {"X-Demo-User-Id": "U001", "Idempotency-Key": "api-review-create-v1"}
    payload = {"order_id": "O1002", "request_type": "refund", "reason": "商品有质量问题，无法使用"}
    try:
        client = TestClient(app)
        created = client.post("/review-tickets", headers=headers, json=payload)
        assert created.status_code == 201
        ticket_id = created.json()["id"]
        assert client.get(f"/review-tickets/{ticket_id}", headers={"X-Demo-User-Id": "U002"}).status_code == 403
        assert client.get("/ops/review-tickets", headers={"X-Demo-User-Id": "U001", "X-Demo-Role": "ops_manager"}).status_code == 403
        queue = client.get("/ops/review-tickets", headers={"X-Demo-User-Id": "OPS001"})
        assert queue.status_code == 200
        assert queue.json()[0]["id"] == ticket_id
        metrics = client.get("/ops/metrics/summary", headers={"X-Demo-User-Id": "OPS001"})
        assert metrics.status_code == 200
        assert metrics.json()["tickets_total"] == 1
        assert metrics.json()["manual_intervention_rate"] == 100.0
        claim = client.post(
            f"/ops/review-tickets/{ticket_id}/claim", headers={"X-Demo-User-Id": "OPS001", "Idempotency-Key": "api-review-claim-v1"},
            json={"expected_version": 1},
        )
        assert claim.status_code == 200
        denied = client.post(
            f"/ops/review-tickets/{ticket_id}/decisions", headers={"X-Demo-User-Id": "U002", "Idempotency-Key": "api-review-denied-v1"},
            json={"action": "reject", "expected_version": 2, "reason_code": "NO", "customer_message": "无权"},
        )
        assert denied.status_code == 403
        policy_payload = {"version": "m3-api-policy-v2", "quality_dispute_enabled": True, "quality_dispute_terms": ["故障"], "review_sla_hours": 12}
        assert client.post("/ops/policy-versions", headers={"X-Demo-User-Id": "OPS001", "Idempotency-Key": "api-policy-denied-v1"}, json=policy_payload).status_code == 403
        published = client.post("/ops/policy-versions", headers={"X-Demo-User-Id": "OPS_MANAGER", "Idempotency-Key": "api-policy-publish-v1"}, json=policy_payload)
        assert published.status_code == 201
        assert published.json()["status"] == "published"
        assert client.get("/ops/policy-versions", headers={"X-Demo-User-Id": "OPS001"}).json()[0]["version"] == "m3-api-policy-v2"
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
