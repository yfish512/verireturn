from __future__ import annotations

import hashlib
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .intent import IntentExtractor
from .knowledge import AgentKnowledgeService
from .tools import M1ToolClient, ToolError
from .trace import TraceStore


class AgentState(TypedDict, total=False):
    thread_id: str
    run_id: str
    actor_id: str
    message: str
    message_id: str
    intent: str
    order_id: str
    case_id: int
    request_type: str
    reason: str
    time_slot: str
    tool_sequence: int
    confirmation_id: str
    review_ticket_id: str
    retrieval_id: str
    citations: list[str]
    final_response: str
    intent_resolved: bool
    last_error_code: str


def _stable_key(state: AgentState, operation: str) -> str:
    raw = f"{state['thread_id']}:{state['message_id']}:{operation}".encode()
    return f"m2-{hashlib.sha256(raw).hexdigest()[:48]}"


class AgentGraph:
    def __init__(self, extractor: IntentExtractor, tools: M1ToolClient, traces: TraceStore, knowledge: AgentKnowledgeService):
        self.extractor = extractor
        self.tools = tools
        self.traces = traces
        self.knowledge = knowledge

    def _call(self, state: AgentState, node: str, tool_name: str, arguments: dict[str, Any], function):
        sequence = state.get("tool_sequence", 0) + 1
        request_id = f"{state['run_id']}:{sequence}"
        started = time.perf_counter()
        try:
            result = function(request_id)
        except ToolError as error:
            self.traces.record_tool_call(
                state["run_id"], sequence, node, tool_name, arguments, None, request_id,
                int((time.perf_counter() - started) * 1000), "failed", error.code,
            )
            return None, {"tool_sequence": sequence, "last_error_code": error.code, "final_response": f"业务系统拒绝了该操作：{error.message}（{error.code}）。"}
        self.traces.record_tool_call(
            state["run_id"], sequence, node, tool_name, arguments, result, request_id,
            int((time.perf_counter() - started) * 1000), "succeeded",
        )
        return result, {"tool_sequence": sequence}

    def parse_intent(self, state: AgentState) -> dict:
        # Normal turns are resolved by TaskMemory before graph execution.  The
        # checkpoint therefore only resumes an interrupt and never supplies
        # accidental slots from an earlier conversation turn.
        if state.get("intent_resolved"):
            return {"final_response": None, "confirmation_id": None, "retrieval_id": None, "citations": [], "last_error_code": None}
        try:
            decision = self.extractor.extract(state["message"])
        except Exception:
            return {"final_response": "暂时无法理解这条售后请求，请提供订单号和具体诉求。"}
        return {**decision.model_dump(exclude_none=True), "final_response": None, "confirmation_id": None, "retrieval_id": None, "citations": [], "last_error_code": None}

    def route_intent(self, state: AgentState) -> str:
        if state.get("final_response"):
            return "finalize"
        return {
            "query_order": "read_order",
            "query_logistics": "read_logistics",
            "query_fulfillment": "read_fulfillment",
            "check_eligibility": "check_eligibility",
            "create_after_sales": "create_case",
            "request_manual_review": "create_review_confirmation",
            "schedule_pickup": "schedule_pickup",
            "knowledge_qa": "knowledge_answer",
        }.get(state.get("intent", "unknown"), "unknown")

    def read_order(self, state: AgentState) -> dict:
        if not state.get("order_id"):
            return {"final_response": "请提供订单号，例如 O1001。"}
        result, update = self._call(
            state, "read_order", "get_order", {"order_id": state["order_id"]},
            lambda request_id: self.tools.get_order(state["actor_id"], state["order_id"], request_id),
        )
        if result is None:
            return update
        return {**update, "final_response": f"订单 {result['id']}：{result['item_name']}，金额 {result['amount']} 元，状态为 {result['status']}。"}

    def read_logistics(self, state: AgentState) -> dict:
        if not state.get("order_id"):
            return {"final_response": "请提供需要查询物流的订单号。"}
        result, update = self._call(
            state, "read_logistics", "get_logistics", {"order_id": state["order_id"]},
            lambda request_id: self.tools.get_logistics(state["actor_id"], state["order_id"], request_id),
        )
        if result is None:
            return update
        return {**update, "final_response": f"订单 {result['order_id']} 的物流状态为 {result['status']}，运单号 {result['tracking_number']}。"}

    def read_fulfillment(self, state: AgentState) -> dict:
        if not state.get("case_id"):
            return {"final_response": "请提供售后单编号，例如“售后单 #12 进度”。"}
        result, update = self._call(
            state, "read_fulfillment", "get_fulfillment_status", {"case_id": state["case_id"]},
            lambda request_id: self.tools.get_fulfillment(state["actor_id"], state["case_id"], request_id),
        )
        if result is None:
            return update
        latest = result["events"][-1]["event_type"] if result.get("events") else "尚未收到履约回调"
        return {**update, "final_response": f"售后单 #{result['case_id']} 当前状态为 {result['status']}；最新可信履约事件：{latest}。"}

    def check_eligibility(self, state: AgentState) -> dict:
        if not state.get("order_id"):
            return {"final_response": "请提供订单号，我才能判断售后资格。"}
        if not state.get("request_type"):
            return {"final_response": "请说明您想退款还是换货。"}
        reason = state.get("reason", "用户请求查询售后资格")
        result, update = self._call(
            state, "check_eligibility", "check_after_sales_eligibility",
            {"order_id": state["order_id"], "request_type": state["request_type"]},
            lambda request_id: self.tools.check_eligibility(state["actor_id"], state["order_id"], state["request_type"], reason, request_id),
        )
        if result is None:
            return update
        if result["eligible"]:
            return {**update, "final_response": f"可以申请{ '退款' if state['request_type'] == 'refund' else '换货' }，可处理金额为 {result['eligible_amount']} 元。"}
        return {**update, "final_response": f"暂不符合售后条件：{result['explanation']}（{result['policy_code']}）。"}

    def create_case(self, state: AgentState) -> dict:
        if not state.get("order_id"):
            return {"final_response": "请提供订单号，例如 O1001。"}
        if not state.get("request_type"):
            return {"final_response": "请说明您想退款还是换货。"}
        # The model may summarize away evidence terms (for example “损坏”).
        # Policy routing must evaluate the immutable user input, not that
        # lossy summary; the model only chooses this bounded workflow.
        reason = state.get("reason") or state["message"]
        eligibility, update = self._call(
            state, "create_case", "check_after_sales_eligibility",
            {"order_id": state["order_id"], "request_type": state["request_type"]},
            lambda request_id: self.tools.check_eligibility(state["actor_id"], state["order_id"], state["request_type"], reason, request_id),
        )
        if eligibility is None:
            return update
        if not eligibility["eligible"]:
            return {**update, "final_response": f"暂不符合售后条件：{eligibility['explanation']}（{eligibility['policy_code']}）。"}
        created, update = self._call(
            {**state, **update}, "create_case", "create_after_sales_case",
            {"order_id": state["order_id"], "request_type": state["request_type"]},
            lambda request_id: self.tools.create_case(
                state["actor_id"], state["order_id"], state["request_type"], reason, request_id, _stable_key(state, "create"),
            ),
        )
        if created is None:
            return update
        return {**update, "case_id": created["id"], "final_response": f"已创建待确认售后单 #{created['id']}，金额 {created['eligible_amount']} 元。"}

    def after_create(self, state: AgentState) -> str:
        return "create_confirmation" if state.get("case_id") and not state.get("final_response", "").startswith("暂不") else "finalize"

    def create_confirmation(self, state: AgentState) -> dict:
        if state.get("confirmation_id"):
            return {}
        confirmation = self.traces.create_confirmation(state["thread_id"], state["run_id"], state["actor_id"], state["case_id"])
        response = f"{state['final_response']} 请通过确认接口明确确认或取消，确认编号：{confirmation.id}。"
        self.traces.finish_run(state["run_id"], "awaiting_confirmation", response)
        return {"confirmation_id": confirmation.id, "final_response": response}

    def create_review_confirmation(self, state: AgentState) -> dict:
        if not state.get("order_id"):
            return {"final_response": "请提供订单号，我才能提交人工审核。"}
        if not state.get("request_type"):
            return {"final_response": "请说明您申请退款还是换货。"}
        if state.get("confirmation_id"):
            return {}
        reason = state.get("reason") or state["message"]
        confirmation = self.traces.create_review_confirmation(
            state["thread_id"], state["run_id"], state["actor_id"], state["order_id"], state["request_type"], reason,
        )
        response = (
            f"将为订单 {state['order_id']} 提交{ '退款' if state['request_type'] == 'refund' else '换货' }人工审核。"
            f"原因：{reason}。确认后才会进入运营队列，确认编号：{confirmation.id}。"
        )
        self.traces.finish_run(state["run_id"], "awaiting_confirmation", response)
        return {"confirmation_id": confirmation.id, "final_response": response}

    def after_review_confirmation(self, state: AgentState) -> str:
        return "await_review_confirmation" if state.get("confirmation_id") else "finalize"

    def await_confirmation(self, state: AgentState) -> dict:
        decision = interrupt({"confirmation_id": state["confirmation_id"], "case_id": state["case_id"], "summary": state["final_response"]})
        approved = isinstance(decision, dict) and decision.get("approved") is True
        operation = "confirm" if approved else "cancel"
        result, update = self._call(
            state, "resolve_confirmation", f"{operation}_after_sales_case", {"case_id": state["case_id"], "approved": approved},
            lambda request_id: (
                self.tools.confirm_case(state["actor_id"], state["case_id"], request_id, _stable_key(state, operation))
                if approved else self.tools.cancel_case(state["actor_id"], state["case_id"], request_id, _stable_key(state, operation))
            ),
        )
        if result is None:
            return update
        self.traces.resolve_confirmation(state["confirmation_id"], approved)
        if approved:
            return {**update, "final_response": f"售后单 #{result['id']} 已确认。请告知可取件的时段，例如“明天上午取件”。"}
        return {**update, "final_response": f"售后单 #{result['id']} 已取消。"}

    def await_review_confirmation(self, state: AgentState) -> dict:
        decision = interrupt({"confirmation_id": state["confirmation_id"], "summary": state["final_response"], "type": "review_submission"})
        approved = isinstance(decision, dict) and decision.get("approved") is True
        if not approved:
            self.traces.resolve_review_confirmation(state["confirmation_id"], False)
            return {"final_response": "已取消提交人工审核。"}
        reason = state.get("reason") or state["message"]
        ticket, update = self._call(
            state, "submit_review_ticket", "create_review_ticket",
            {"order_id": state["order_id"], "request_type": state["request_type"]},
            lambda request_id: self.tools.create_review_ticket(
                state["actor_id"], state["order_id"], state["request_type"], reason, request_id, _stable_key(state, "review_ticket"),
            ),
        )
        if ticket is None:
            return update
        self.traces.resolve_review_confirmation(state["confirmation_id"], True, ticket["id"])
        return {**update, "review_ticket_id": ticket["id"], "final_response": f"人工审核工单 #{ticket['id']} 已提交，预计处理时限为 {ticket['due_at']}。"}

    def schedule_pickup(self, state: AgentState) -> dict:
        if not state.get("case_id"):
            return {"final_response": "请提供售后单编号，或在确认后继续使用原会话预约取件。"}
        if not self._valid_time_slot(state.get("time_slot")):
            return {"final_response": "请提供取件时段，例如“2026-09-15 上午”。"}
        result, update = self._call(
            state, "schedule_pickup", "schedule_pickup", {"case_id": state["case_id"], "time_slot": state["time_slot"]},
            lambda request_id: self.tools.schedule_pickup(
                state["actor_id"], state["case_id"], state["time_slot"], request_id, _stable_key(state, "schedule_pickup"),
            ),
        )
        if result is None:
            return update
        return {**update, "final_response": f"售后单 #{result['id']} 已预约取件，时段为 {result['pickup_slot']}。"}

    def knowledge_answer(self, state: AgentState) -> dict:
        sequence = state.get("tool_sequence", 0) + 1
        request_id = f"{state['run_id']}:{sequence}"
        started = time.perf_counter()
        try:
            result = self.knowledge.answer(state["actor_id"], state["run_id"], state["message"])
        except Exception:
            self.traces.record_tool_call(
                state["run_id"], sequence, "knowledge_answer", "retrieve_knowledge", {"route": "knowledge_qa"}, None,
                request_id, int((time.perf_counter() - started) * 1000), "failed", "KNOWLEDGE_RETRIEVAL_FAILED",
            )
            return {"tool_sequence": sequence, "final_response": "知识检索暂时不可用，请稍后重试或提供订单号查询具体售后。"}
        self.traces.record_tool_call(
            state["run_id"], sequence, "knowledge_answer", "retrieve_knowledge", {"route": "knowledge_qa"},
            {"retrieval_id": result.retrieval_id, "hit_count": result.hit_count, "citations": result.citations}, request_id,
            int((time.perf_counter() - started) * 1000), "succeeded",
        )
        return {
            "tool_sequence": sequence, "retrieval_id": result.retrieval_id, "citations": result.citations,
            "final_response": result.response,
        }

    @staticmethod
    def _valid_time_slot(time_slot: str | None) -> bool:
        if not time_slot:
            return False
        return any(marker in time_slot for marker in ("今天", "明天", "后天", "上午", "下午", "晚上", "点", "月", "日", "-"))

    def unknown(self, _: AgentState) -> dict:
        return {"final_response": "我可以查询订单或物流、判断退款/换货资格、创建售后申请、预约取件，并解释已发布的售后政策。请提供订单号或具体问题。"}

    def finalize(self, state: AgentState) -> dict:
        self.traces.finish_run(state["run_id"], "completed", state.get("final_response", "处理完成。"))
        return {}

    def build(self, checkpointer):
        graph = StateGraph(AgentState)
        graph.add_node("parse_intent", self.parse_intent)
        graph.add_node("read_order", self.read_order)
        graph.add_node("read_logistics", self.read_logistics)
        graph.add_node("read_fulfillment", self.read_fulfillment)
        graph.add_node("check_eligibility", self.check_eligibility)
        graph.add_node("create_case", self.create_case)
        graph.add_node("create_confirmation", self.create_confirmation)
        graph.add_node("await_confirmation", self.await_confirmation)
        graph.add_node("create_review_confirmation", self.create_review_confirmation)
        graph.add_node("await_review_confirmation", self.await_review_confirmation)
        graph.add_node("schedule_pickup", self.schedule_pickup)
        graph.add_node("knowledge_answer", self.knowledge_answer)
        graph.add_node("unknown", self.unknown)
        graph.add_node("finalize", self.finalize)
        graph.add_edge(START, "parse_intent")
        graph.add_conditional_edges("parse_intent", self.route_intent)
        graph.add_conditional_edges("create_case", self.after_create)
        graph.add_conditional_edges("create_review_confirmation", self.after_review_confirmation)
        for node in ("read_order", "read_logistics", "read_fulfillment", "check_eligibility", "schedule_pickup", "knowledge_answer", "unknown"):
            graph.add_edge(node, "finalize")
        graph.add_edge("create_confirmation", "await_confirmation")
        graph.add_edge("await_confirmation", "finalize")
        graph.add_edge("await_review_confirmation", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=checkpointer)
