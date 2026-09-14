from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base, get_db
from backend.app.main import app
from backend.app.seed import seed_demo_data


JWT_SECRET = "test-jwt-secret-with-at-least-thirty-two-characters"


def test_jwt_mode_uses_verified_subject_and_ignores_demo_identity_header(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'auth-test.db'}", connect_args={"check_same_thread": False})
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

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "jwt")
    monkeypatch.setenv("AUTH_JWT_SECRET", JWT_SECRET)
    token = jwt.encode({"sub": "U001", "exp": datetime.now(timezone.utc) + timedelta(minutes=5), "role": "ops_manager"}, JWT_SECRET, algorithm="HS256")
    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {token}", "X-Demo-User-Id": "U002", "X-Demo-Role": "ops_manager"}
        assert client.get("/tools/orders/O1001", headers=headers).status_code == 200
        # The JWT subject U001 owns O1001. The fake header U002 is ignored.
        assert client.get("/tools/orders/O1004", headers=headers).status_code == 403
        assert client.get("/tools/orders/O1001", headers={"X-Demo-User-Id": "U001"}).status_code == 401
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_production_refuses_demo_auth_mode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "demo")
    client = TestClient(app)
    response = client.get("/tools/orders/O1001", headers={"X-Demo-User-Id": "U001"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PRODUCTION_JWT_REQUIRED"
