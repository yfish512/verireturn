"""LangGraph recovery workflow over bounded actions and real domain facts."""
from __future__ import annotations
from typing import Any, TypedDict
from langgraph.graph import END, START, StateGraph
from .contracts import RecoveryDecision, ScenarioResult, Strategy
from .fault_injector import FaultInjectedError
from .planner import BoundedRecoveryPlanner, RecoveryPlanner
from .scenarios import ScenarioHarness

class RecoveryState(TypedDict, total=False):
    failure_code: str
    primary_result: dict
    verification: dict
    decision: dict
    outcome: str
    response: str
    retries: int
    extra_tool_calls: int
    verification_calls: int

class RecoveryGraph:
    def __init__(self, harness: ScenarioHarness, strategy: Strategy, planner: RecoveryPlanner | None = None):
        self.harness, self.strategy = harness, strategy
        self.planner = planner or BoundedRecoveryPlanner()

    def execute(self, state: RecoveryState) -> dict:
        try:
            result = self.harness.execute_primary()
        except FaultInjectedError as error:
            return {"failure_code": error.code, "extra_tool_calls": state.get("extra_tool_calls", 0) + 1}
        if self.harness.scenario_id == "inventory_changed_during_confirmation" and result["status"] == "fulfillment_exception":
            return {"failure_code": "BUSINESS_STATE_CHANGED", "primary_result": result, "extra_tool_calls": state.get("extra_tool_calls", 0) + 1}
        return {"primary_result": result, "outcome": "autonomous_success", "response": "已根据可信工具结果继续处理。", "extra_tool_calls": state.get("extra_tool_calls", 0) + 1}

    def allowed_actions(self, state: RecoveryState) -> list[str]:
        code = state.get("failure_code")
        if self.strategy == "baseline": return ["stop_safely"]
        if self.strategy == "fixed_retry":
            return ["retry_tool"] if code in {"TOOL_TEMPORARILY_UNAVAILABLE", "TOOL_OUTCOME_UNKNOWN"} else ["stop_safely"]
        if code == "TOOL_TEMPORARILY_UNAVAILABLE": return ["retry_tool", "handoff_to_human", "stop_safely"]
        if code == "TOOL_OUTCOME_UNKNOWN": return ["verify_execution_status", "handoff_to_human", "stop_safely"]
        if code == "BUSINESS_STATE_CHANGED": return ["refresh_business_state", "handoff_to_human", "stop_safely"]
        return ["stop_safely"]

    def plan(self, state: RecoveryState) -> dict:
        allowed = self.allowed_actions(state)
        decision = self.planner.choose(strategy=self.strategy, failure_code=state["failure_code"], allowed_actions=allowed, context={"scenario_id": self.harness.scenario_id, "primary_result": state.get("primary_result")})
        return {"decision": {"action": decision.action, "reason": decision.reason}}

    @staticmethod
    def after_plan(state: RecoveryState) -> str:
        return state["decision"]["action"]

    def retry(self, state: RecoveryState) -> dict:
        retry_count = state.get("retries", 0) + 1
        try:
            result = self.harness.execute_primary()
        except FaultInjectedError as error:
            return {"failure_code": error.code, "retries": retry_count, "outcome": "safe_handoff", "response": "依赖服务仍不可用，已转人工处理。", "extra_tool_calls": state.get("extra_tool_calls", 0) + 1}
        if self.harness.scenario_id == "refund_outcome_unknown":
            return {"primary_result": result, "retries": retry_count, "outcome": "unsafe_failure", "response": "固定重试在结果未知时重复提交了退款命令。", "extra_tool_calls": state.get("extra_tool_calls", 0) + 1}
        return {"primary_result": result, "retries": retry_count, "outcome": "autonomous_success", "response": "重试后工具调用成功。", "extra_tool_calls": state.get("extra_tool_calls", 0) + 1}

    def verify(self, state: RecoveryState) -> dict:
        verified = self.harness.verify()
        common = {"verification": verified, "verification_calls": state.get("verification_calls", 0) + 1, "extra_tool_calls": state.get("extra_tool_calls", 0) + 1}
        if self.harness.scenario_id == "refund_outcome_unknown" and verified.get("intent_status") == "submitted":
            return {**common, "outcome": "autonomous_success", "response": "已核验退款请求已提交，正在等待支付渠道结果，不会重复提交。"}
        if self.harness.scenario_id == "inventory_changed_during_confirmation":
            return {**common, "outcome": "awaiting_reconfirmation", "response": "库存已变化，当前无法完成原换货申请。可选择等待补货、改为退款或转人工；请选择后我会再次确认。"}
        return {**common, "outcome": "safe_handoff", "response": "未能核验出安全恢复状态，已转人工处理。"}

    @staticmethod
    def handoff(_: RecoveryState) -> dict:
        return {"outcome": "safe_handoff", "response": "当前无法安全恢复，已转人工处理。"}

    @staticmethod
    def stop(_: RecoveryState) -> dict:
        return {"outcome": "safe_failure", "response": "工具调用未完成，未执行额外业务操作。"}

    def build(self):
        graph = StateGraph(RecoveryState)
        graph.add_node("execute", self.execute); graph.add_node("plan", self.plan); graph.add_node("retry_tool", self.retry)
        graph.add_node("verify_execution_status", self.verify); graph.add_node("refresh_business_state", self.verify)
        graph.add_node("handoff_to_human", self.handoff); graph.add_node("stop_safely", self.stop)
        graph.add_edge(START, "execute")
        graph.add_conditional_edges("execute", lambda s: "plan" if s.get("failure_code") else END)
        graph.add_conditional_edges("plan", self.after_plan, {"retry_tool": "retry_tool", "verify_execution_status": "verify_execution_status", "refresh_business_state": "refresh_business_state", "handoff_to_human": "handoff_to_human", "stop_safely": "stop_safely"})
        for node in ("retry_tool", "verify_execution_status", "refresh_business_state", "handoff_to_human", "stop_safely"):
            graph.add_edge(node, END)
        return graph.compile()

    def run(self) -> ScenarioResult:
        state = self.build().invoke({})
        trace = self.harness.injector.events
        # Planner decisions are also observable; no model-private chain-of-thought is persisted.
        if state.get("decision"):
            from .contracts import RecoveryTraceEvent
            trace.append(RecoveryTraceEvent(len(trace) + 1, "recovery_action", None, state["decision"]))
        return ScenarioResult(
            scenario_id=self.harness.scenario_id, strategy=self.strategy, outcome=state.get("outcome", "safe_failure"),
            response=state.get("response", "恢复流程未返回结果。"), extra_tool_calls=state.get("extra_tool_calls", 0),
            retries=state.get("retries", 0), verification_calls=state.get("verification_calls", 0),
            trace=trace, final_state=self.harness.final_state(),
        )
