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

    def create_thread(self, actor_id):
        self.calls.append(("create_thread", actor_id))
        return {"thread_id": "thread-created"}

    def thread_snapshot(self, thread_id, actor_id, *, before_sequence=None, limit=30):
        self.calls.append(("snapshot", thread_id, actor_id, before_sequence, limit))
        return {"thread_id": thread_id, "messages": [], "task": None, "next_before_sequence": None}

    def cancel_task(self, thread_id, actor_id):
        self.calls.append(("cancel_task", thread_id, actor_id))
        return {"thread_id": thread_id, "response": "已放弃当前任务。", "task": None}


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


def test_agent_thread_endpoints_are_customer_scoped():
    runtime = FakeRuntime()
    app.dependency_overrides[get_agent_runtime] = lambda: runtime
    try:
        client = TestClient(app)
        headers = {"X-Demo-User-Id": "U001"}
        created = client.post("/agent/threads", headers=headers)
        assert created.status_code == 201
        assert created.json()["thread_id"] == "thread-created"
        snapshot = client.get("/agent/threads/thread-created", headers=headers)
        assert snapshot.status_code == 200
        assert snapshot.json()["messages"] == []
        assert runtime.calls == [("create_thread", "U001"), ("snapshot", "thread-created", "U001", None, 30)]
        cancelled = client.post("/agent/threads/thread-created/task/cancel", headers=headers)
        assert cancelled.status_code == 200
        assert runtime.calls[-1] == ("cancel_task", "thread-created", "U001")
    finally:
        app.dependency_overrides.clear()
