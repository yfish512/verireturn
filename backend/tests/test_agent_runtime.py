from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from langgraph.checkpoint.memory import MemorySaver

from backend.app.agent.intent import KeywordIntentExtractor, OpenAIIntentExtractor
from backend.app.agent.knowledge import AgentKnowledgeService, ExtractiveAnswerGenerator
from backend.app.agent.runtime import AgentSettings, build_agent_runtime
from backend.app.agent.trace import TraceStore
from backend.app.agent.tools import ToolError
from backend.app.database import Base
from backend.app.domain.knowledge import EMBEDDING_DIMENSIONS, create_document, process_one_ingestion_job, publish_version, queue_ingestion
from backend.app.models import AgentConfirmation, AgentReviewConfirmation, AgentRun, AgentToolCall
from backend.app.schemas import KnowledgeDocumentCreateRequest
from backend.app.seed import seed_demo_data


class FakeM1Tools:
    def __init__(self):
        self.calls = []

    def check_eligibility(self, actor_id, order_id, request_type, reason, request_id):
        self.calls.append(("check_eligibility", actor_id, order_id, request_id))
        if order_id == "O10001":
            raise ToolError("ORDER_NOT_FOUND", "订单不存在。", 404)
        return {"eligible": True, "eligible_amount": "299.00", "policy_code": "RETURN_WITHIN_7_DAYS", "explanation": "可退款"}

    def create_case(self, actor_id, order_id, request_type, reason, request_id, idempotency_key):
        self.calls.append(("create_case", actor_id, order_id, idempotency_key))
        return {"id": 42, "eligible_amount": "299.00", "status": "pending_confirmation"}

    def confirm_case(self, actor_id, case_id, request_id, idempotency_key):
        self.calls.append(("confirm_case", actor_id, case_id, idempotency_key))
        return {"id": case_id, "status": "awaiting_pickup"}

    def cancel_case(self, actor_id, case_id, request_id, idempotency_key):
        self.calls.append(("cancel_case", actor_id, case_id, idempotency_key))
        return {"id": case_id, "status": "cancelled"}

    def schedule_pickup(self, actor_id, case_id, time_slot, request_id, idempotency_key):
        self.calls.append(("schedule_pickup", actor_id, case_id, time_slot))
        return {"id": case_id, "status": "pickup_scheduled", "pickup_slot": time_slot}

    def get_order(self, actor_id, order_id, request_id):
        self.calls.append(("get_order", actor_id, order_id))
        return {"id": order_id, "item_name": "耳机", "amount": "299.00", "status": "delivered"}

    def get_logistics(self, actor_id, order_id, request_id):
        self.calls.append(("get_logistics", actor_id, order_id))
        return {"order_id": order_id, "status": "delivered", "tracking_number": "SF1"}

    def get_fulfillment(self, actor_id, case_id, request_id):
        self.calls.append(("get_fulfillment", actor_id, case_id))
        return {"case_id": case_id, "status": "picked_up", "events": [{"event_type": "pickup.collected"}], "notifications": []}

    def create_review_ticket(self, actor_id, order_id, request_type, reason, request_id, idempotency_key):
        self.calls.append(("create_review_ticket", actor_id, order_id, idempotency_key))
        return {"id": "review-ticket-1", "status": "open", "due_at": "2026-09-15T12:00:00+00:00"}


class FakeEmbeddings:
    model_name = "agent-test-embedding-512"
    dimensions = EMBEDDING_DIMENSIONS

    def embed(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for character in text:
                vector[ord(character) % self.dimensions] += 1.0
            vectors.append(vector)
        return vectors


def runtime(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agent-runtime.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_demo_data(db)
    tools = FakeM1Tools()
    agent = build_agent_runtime(
        settings=AgentSettings(agent_service_url="http://unused"),
        session_factory=sessions,
        tools=tools,
        extractor=KeywordIntentExtractor(),
        checkpointer=MemorySaver(),
    )
    return agent, tools, sessions



class FakeIntentClient:
    def __init__(self, content: str):
        self.content = content
        self.calls: list[dict] = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def test_live_intent_fast_routes_clear_action_without_model_call():
    extractor = OpenAIIntentExtractor("http://unused", "test-key", "test-model")
    client = FakeIntentClient('{"intent":"knowledge_qa"}')
    extractor.client = client

    decision = extractor.extract("能帮我退一个没拆封的商品吗")

    assert decision.intent == "create_after_sales"
    assert decision.request_type == "refund"
    assert client.calls == []


def test_live_intent_keeps_policy_question_on_knowledge_route():
    extractor = OpenAIIntentExtractor("http://unused", "test-key", "test-model")
    client = FakeIntentClient('{"intent":"knowledge_qa"}')
    extractor.client = client

    decision = extractor.extract("退款政策和时效是什么？")

    assert decision.intent == "knowledge_qa"
    assert len(client.calls) == 1

def test_agent_requires_structured_confirmation_before_confirming_case(tmp_path):
    agent, tools, sessions = runtime(tmp_path)
    result = agent.handle_message("thread-refund-1", "U001", "帮我退 O1001，商品没有拆封", "message-refund-1")

    assert result["status"] == "awaiting_confirmation"
    assert result["case_id"] == 42
    assert [call[0] for call in tools.calls] == ["check_eligibility", "create_case"]
    with sessions() as db:
        confirmation = db.get(AgentConfirmation, result["confirmation_id"])
        assert confirmation.status == "pending"

    resolved = agent.resolve_confirmation(result["confirmation_id"], "U001", True)
    assert resolved["status"] == "completed"
    assert resolved["response"] == "已提交退款申请。请提供上门取件时间，例如“明天上午”。"
    assert [call[0] for call in tools.calls] == ["check_eligibility", "create_case", "confirm_case"]
    with sessions() as db:
        assert db.get(AgentConfirmation, result["confirmation_id"]).status == "approved"
        assert db.get(AgentRun, result["run_id"]).status == "completed"
        assert len(list(db.scalars(select(AgentToolCall).where(AgentToolCall.run_id == result["run_id"])))) == 3


def test_agent_can_resume_same_thread_to_schedule_pickup(tmp_path):
    agent, tools, _ = runtime(tmp_path)
    created = agent.handle_message("thread-pickup-1", "U001", "帮我退 O1001", "message-create-1")
    agent.resolve_confirmation(created["confirmation_id"], "U001", True)

    scheduled = agent.handle_message("thread-pickup-1", "U001", "明天上午取件", "message-pickup-1")
    assert scheduled["status"] == "completed"
    assert "已预约取件" in scheduled["response"]
    assert tools.calls[-1][0] == "schedule_pickup"


def test_confirmation_cannot_be_resolved_by_another_user(tmp_path):
    agent, tools, _ = runtime(tmp_path)
    created = agent.handle_message("thread-auth-1", "U001", "帮我退 O1001", "message-auth-1")
    try:
        agent.resolve_confirmation(created["confirmation_id"], "U002", True)
    except Exception as error:
        assert getattr(error, "code") == "CONFIRMATION_NOT_FOUND"
    else:
        raise AssertionError("其他用户不应确认该售后单")
    assert "confirm_case" not in [call[0] for call in tools.calls]


def test_rejected_confirmation_calls_cancel_instead_of_confirm(tmp_path):
    agent, tools, sessions = runtime(tmp_path)
    created = agent.handle_message("thread-reject-1", "U001", "帮我退 O1001", "message-reject-1")
    resolved = agent.resolve_confirmation(created["confirmation_id"], "U001", False)
    assert resolved["status"] == "completed"
    assert resolved["response"] == "已取消本次退款申请。"
    assert [call[0] for call in tools.calls] == ["check_eligibility", "create_case", "cancel_case"]
    with sessions() as db:
        assert db.get(AgentConfirmation, created["confirmation_id"]).status == "rejected"


def test_duplicate_message_while_pending_returns_original_confirmation_without_new_write(tmp_path):
    agent, tools, sessions = runtime(tmp_path)
    first = agent.handle_message("thread-duplicate-1", "U001", "帮我退 O1001", "message-duplicate-1")
    duplicate = agent.handle_message("thread-duplicate-1", "U001", "帮我退 O1001", "message-duplicate-1")
    assert duplicate["confirmation_id"] == first["confirmation_id"]
    assert duplicate["run_id"] == first["run_id"]
    assert [call[0] for call in tools.calls] == ["check_eligibility", "create_case"]
    with sessions() as db:
        assert len(list(db.scalars(select(AgentRun)))) == 1


def test_agent_requests_a_specific_pickup_slot(tmp_path):
    agent, tools, _ = runtime(tmp_path)
    created = agent.handle_message("thread-slot-1", "U001", "帮我退 O1001", "message-slot-create")
    agent.resolve_confirmation(created["confirmation_id"], "U001", True)
    response = agent.handle_message("thread-slot-1", "U001", "取件", "message-slot-missing")
    assert "取件时段" in response["response"]
    assert "schedule_pickup" not in [call[0] for call in tools.calls]


def test_unsupported_business_request_never_calls_a_tool(tmp_path):
    agent, tools, _ = runtime(tmp_path)
    result = agent.handle_message("thread-unsupported-1", "U001", "给 O1001 补开发票", "message-unsupported")
    assert "可以查询订单" in result["response"]
    assert tools.calls == []


def test_agent_requires_confirmation_before_submitting_review_ticket(tmp_path):
    agent, tools, sessions = runtime(tmp_path)
    created = agent.handle_message(
        "thread-review-1", "U001", "O1002 有质量问题无法使用，我要退款并申请人工审核", "message-review-1"
    )
    assert created["status"] == "awaiting_confirmation"
    assert created["case_id"] is None
    assert tools.calls == []
    with sessions() as db:
        assert db.get(AgentReviewConfirmation, created["confirmation_id"]).status == "pending"

    resolved = agent.resolve_confirmation(created["confirmation_id"], "U001", True)
    assert resolved["status"] == "completed"
    assert resolved["ticket_id"] == "review-ticket-1"
    assert [call[0] for call in tools.calls] == ["create_review_ticket"]
    with sessions() as db:
        confirmation = db.get(AgentReviewConfirmation, created["confirmation_id"])
        assert confirmation.status == "approved"
        assert confirmation.ticket_id == "review-ticket-1"


def test_quality_exchange_remains_on_the_deterministic_after_sales_path(tmp_path):
    agent, tools, _ = runtime(tmp_path)
    result = agent.handle_message("thread-quality-exchange-1", "U001", "O1003 有质量问题，申请换货", "message-quality-exchange-1")
    assert result["case_id"] == 42
    assert [call[0] for call in tools.calls] == ["check_eligibility", "create_case"]


def test_agent_knowledge_answer_has_a_valid_citation_and_never_calls_m1_tools(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agent-knowledge.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_demo_data(db)
        _, version = create_document(db, "OPS_MANAGER", KnowledgeDocumentCreateRequest(
            stable_key="agent-refund-policy", title="退款政策", audience="customer", category="after-sales",
            content_markdown="# 退款时效\n\n签收后七天内且未拆封的商品，可以提交退款申请。",
        ), "agent-knowledge-create-v1")
        queue_ingestion(db, "OPS_MANAGER", version.id, "agent-knowledge-queue-v1")
        process_one_ingestion_job(db, FakeEmbeddings())
        publish_version(db, "OPS_MANAGER", version.id, "agent-knowledge-publish-v1")
    tools = FakeM1Tools()
    knowledge = AgentKnowledgeService(sessions, ExtractiveAnswerGenerator(), embedding_provider=FakeEmbeddings())
    agent = build_agent_runtime(
        settings=AgentSettings(agent_service_url="http://unused"), session_factory=sessions, tools=tools,
        extractor=KeywordIntentExtractor(), checkpointer=MemorySaver(), knowledge_service=knowledge,
    )
    result = agent.handle_message("knowledge-thread-1", "U001", "退款政策和时效是什么", "knowledge-message-1")
    assert result["status"] == "completed"
    assert result["retrieval_id"]
    assert len(result["citations"]) == 1
    assert "依据：" not in result["response"]
    assert len(result["response"]) <= 200
    assert tools.calls == []
    with sessions() as db:
        calls = list(db.scalars(select(AgentToolCall).where(AgentToolCall.run_id == result["run_id"])))
        assert [(call.tool_name, call.status) for call in calls] == [("retrieve_knowledge", "succeeded")]


def test_agent_reads_trusted_fulfillment_fact_without_write_tool(tmp_path):
    agent, tools, sessions = runtime(tmp_path)
    result = agent.handle_message("fulfillment-thread-1", "U001", "售后单 #42 进度到哪了", "fulfillment-message-1")
    assert result["status"] == "completed"
    assert "picked_up" in result["response"]
    assert [call[0] for call in tools.calls] == ["get_fulfillment"]
    with sessions() as db:
        calls = list(db.scalars(select(AgentToolCall).where(AgentToolCall.run_id == result["run_id"])))
        assert [(call.tool_name, call.status) for call in calls] == [("get_fulfillment_status", "succeeded")]


def test_agent_retains_refund_task_through_wrong_then_correct_order_number(tmp_path):
    agent, tools, _ = runtime(tmp_path)
    first = agent.handle_message("thread-memory-repair", "U001", "你帮我退一下商品吧，没拆封", "memory-message-1")
    assert first["status"] == "completed"
    assert first["response"] == "可以，请提供订单号，例如 O1001。"
    assert first["memory"]["intent"] == "create_after_sales"
    assert first["memory"]["slots"]["request_type"] == "refund"
    assert first["memory"]["slots"]["reason"] == "你帮我退一下商品吧，没拆封"
    assert tools.calls == []

    wrong = agent.handle_message("thread-memory-repair", "U001", "O10001", "memory-message-2")
    assert wrong["response"] == "未找到 O10001，请确认订单号。"
    assert wrong["memory"]["phase"] == "collecting_slots"
    assert wrong["memory"]["slots"].get("order_id") is None

    resumed = agent.handle_message("thread-memory-repair", "U001", "O1001", "memory-message-3")
    assert resumed["status"] == "awaiting_confirmation"
    assert resumed["case_id"] == 42
    assert [call[0] for call in tools.calls] == ["check_eligibility", "check_eligibility", "create_case"]


def test_agent_thread_memory_is_owned_by_the_customer(tmp_path):
    agent, _, _ = runtime(tmp_path)
    agent.handle_message("thread-memory-owner", "U001", "帮我退，没拆封", "memory-owner-1")
    try:
        agent.handle_message("thread-memory-owner", "U002", "O1001", "memory-owner-2")
    except Exception as error:
        assert getattr(error, "code") == "THREAD_NOT_FOUND"
    else:
        raise AssertionError("其他客户不应读取或污染会话记忆")


def test_new_write_request_replaces_pickup_task_instead_of_reusing_old_case(tmp_path):
    agent, tools, _ = runtime(tmp_path)
    created = agent.handle_message("thread-memory-new-task", "U001", "帮我退 O1001", "memory-new-task-1")
    agent.resolve_confirmation(created["confirmation_id"], "U001", True)
    replacement = agent.handle_message("thread-memory-new-task", "U001", "帮我退 O1002", "memory-new-task-2")
    assert replacement["status"] == "awaiting_confirmation"
    assert replacement["memory"]["intent"] == "create_after_sales"
    assert replacement["memory"]["case_id"] == 42
    assert [call[0] for call in tools.calls].count("create_case") == 2
