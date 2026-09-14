from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api.routes import router
from backend.app.database import Base, get_db
from backend.app.main import app
from backend.app.seed import seed_demo_data


def test_api_uses_server_injected_identity_and_requires_idempotency_key(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api-test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        seed_demo_data(session)

    def override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    headers = {"X-Demo-User-Id": "U001", "Idempotency-Key": "api-create-o1001-v1", "X-Request-Id": "api-trace-1"}
    payload = {"order_id": "O1001", "request_type": "refund", "reason": "商品未拆封，申请退款"}
    try:
        client = TestClient(app)
        created = client.post("/tools/after-sales/cases", json=payload, headers=headers)
        assert created.status_code == 201
        case_id = created.json()["id"]
        assert client.get(f"/tools/after-sales/cases/{case_id}", headers={"X-Demo-User-Id": "U002"}).status_code == 403
        assert client.post("/tools/after-sales/cases", json=payload, headers={"X-Demo-User-Id": "U001"}).status_code == 422
        assert client.post("/tools/after-sales/cases", json={**payload, "user_id": "U002"}, headers=headers).status_code == 422
        audit = client.get(f"/tools/after-sales/cases/{case_id}/audit-logs", headers={"X-Demo-User-Id": "U001"})
        assert audit.status_code == 200
        assert audit.json()[0]["actor_id"] == "U001"
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
