from fastapi.testclient import TestClient

from backend.app.agent.runtime import get_agent_runtime
from backend.app.main import app


class FakeRuntime:
    def __init__(self):
        self.calls = []
        self.traces = self

    def handle_message(self, thread_id, actor_id, message, message_id):
        self.calls.append(("message", thread_id, actor_id, message, message_id))
        return {"thread_id": thread_id, "run_id": "run-1", "status": "awaiting_confirmation", "response": "请确认", "confirmation_id": "confirm-1", "case_id": 1}

    def resolve_confirmation(self, confirmation_id, actor_id, approved):
        self.calls.append(("confirmation", confirmation_id, actor_id, approved))
        return {"thread_id": "thread-1", "run_id": "run-1", "status": "completed", "response": "已确认", "confirmation_id": confirmation_id, "case_id": 1}

    def list_tool_calls(self, run_id, actor_id):
        self.calls.append(("trace", run_id, actor_id))
        return []


def test_agent_http_contract_injects_actor_and_uses_structured_confirmation():
    runtime = FakeRuntime()
    app.dependency_overrides[get_agent_runtime] = lambda: runtime
    try:
        client = TestClient(app)
        headers = {"X-Demo-User-Id": "U001"}
        response = client.post("/agent/threads/thread-1/messages", headers=headers, json={"message": "帮我退 O1001", "message_id": "client-message-001"})
        assert response.status_code == 200
        assert response.json()["status"] == "awaiting_confirmation"
        confirm = client.post("/agent/confirmations/confirm-1", headers=headers, json={"approved": True})
        assert confirm.status_code == 200
        assert client.post("/agent/confirmations/confirm-1", headers=headers, json={"approved": True, "user_id": "U002"}).status_code == 422
        assert runtime.calls[:2] == [
            ("message", "thread-1", "U001", "帮我退 O1001", "client-message-001"),
            ("confirmation", "confirm-1", "U001", True),
        ]
    finally:
        app.dependency_overrides.clear()
