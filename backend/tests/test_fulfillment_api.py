import hashlib
import hmac
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base, get_db
from backend.app.main import app
from backend.app.seed import seed_demo_data


def test_webhook_verifies_raw_hmac_and_idempotently_exposes_customer_facts(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'fulfillment-api.db'}", connect_args={"check_same_thread": False})
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

    secret = "m5-webhook-test-secret"
    monkeypatch.setenv("FULFILLMENT_WEBHOOK_SECRET", secret)
    app.dependency_overrides[get_db] = override_db
    headers = {"X-Demo-User-Id": "U001", "Idempotency-Key": "m5-api-create-0001"}
    try:
        client = TestClient(app)
        created = client.post("/tools/after-sales/cases", json={"order_id": "O1001", "request_type": "refund", "reason": "M5 API 测试"}, headers=headers)
        case_id = created.json()["id"]
        assert client.post(f"/tools/after-sales/cases/{case_id}/confirm", headers={"X-Demo-User-Id": "U001", "Idempotency-Key": "m5-api-confirm-0001"}).status_code == 200
        assert client.post(f"/tools/after-sales/cases/{case_id}/pickup", json={"time_slot": "2026-09-15 上午"}, headers={"X-Demo-User-Id": "U001", "Idempotency-Key": "m5-api-pickup-0001"}).status_code == 200
        event = {"event_id": "api-pickup-0001", "case_id": case_id, "event_type": "pickup.collected", "sequence_no": 1, "occurred_at": datetime.now(timezone.utc).isoformat(), "payload": {"tracking": "M5API"}}
        raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
        signed = {
            "Content-Type": "application/json", "X-Provider-Event-Id": event["event_id"],
            "X-Provider-Timestamp": datetime.now(timezone.utc).isoformat(),
            "X-Provider-Signature": "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
        }
        accepted = client.post("/internal/fulfillment/webhooks/demo_fulfillment", content=raw, headers=signed)
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "applied"
        assert client.post("/internal/fulfillment/webhooks/demo_fulfillment", content=raw, headers=signed).json()["status"] == "applied"
        facts = client.get(f"/tools/after-sales/cases/{case_id}/fulfillment", headers={"X-Demo-User-Id": "U001"})
        assert facts.status_code == 200
        assert facts.json()["status"] == "picked_up"
        assert len(facts.json()["events"]) == 1
        bad = client.post("/internal/fulfillment/webhooks/demo_fulfillment", content=raw, headers={**signed, "X-Provider-Signature": "sha256=wrong"})
        assert bad.status_code == 403
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_legacy_direct_completion_is_disabled_without_explicit_demo_switch(monkeypatch):
    monkeypatch.setenv("INTERNAL_CALLBACK_TOKEN", "test-internal-token")
    monkeypatch.delenv("LEGACY_FULFILLMENT_SIMULATOR_ENABLED", raising=False)
    client = TestClient(app)
    response = client.post(
        "/tools/internal/after-sales/cases/1/complete",
        headers={"X-Internal-Service-Key": "test-internal-token", "Idempotency-Key": "legacy-complete-0001"},
    )
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "LEGACY_FULFILLMENT_SIMULATOR_DISABLED"
