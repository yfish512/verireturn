"""Bounded recovery planning. LLM planners can only choose from supplied actions."""
from __future__ import annotations
import json
from typing import Protocol
from openai import OpenAI
from .contracts import RecoveryDecision, Strategy

class RecoveryPlanner(Protocol):
    def choose(self, *, strategy: Strategy, failure_code: str, allowed_actions: list[str], context: dict) -> RecoveryDecision: ...

class BoundedRecoveryPlanner:
    """Deterministic fallback used for reproducible offline measurements."""
    def choose(self, *, strategy: Strategy, failure_code: str, allowed_actions: list[str], context: dict) -> RecoveryDecision:
        if strategy == "baseline":
            return RecoveryDecision("stop_safely", "基线策略不会根据工具反馈恢复。")
        if strategy == "fixed_retry":
            if "retry_tool" in allowed_actions:
                return RecoveryDecision("retry_tool", "固定重试策略。")
            return RecoveryDecision("stop_safely", "固定重试无可执行动作。")
        if failure_code == "TOOL_TEMPORARILY_UNAVAILABLE" and "retry_tool" in allowed_actions:
            return RecoveryDecision("retry_tool", "临时读取失败，执行受限重试。")
        if failure_code == "TOOL_OUTCOME_UNKNOWN" and "verify_execution_status" in allowed_actions:
            return RecoveryDecision("verify_execution_status", "写操作结果未知，必须先核验领域事实。")
        if failure_code == "BUSINESS_STATE_CHANGED" and "refresh_business_state" in allowed_actions:
            return RecoveryDecision("refresh_business_state", "业务状态可能变化，先刷新可信状态。")
        if "handoff_to_human" in allowed_actions:
            return RecoveryDecision("handoff_to_human", "没有安全的自主恢复路径。")
        return RecoveryDecision("stop_safely", "停止以避免扩大业务副作用。")

class OpenAIRecoveryPlanner:
    """Optional model planner; bounded JSON action validation remains local."""
    def __init__(self, base_url: str, api_key: str, model: str):
        self.client, self.model = OpenAI(base_url=base_url, api_key=api_key), model
        self.fallback = BoundedRecoveryPlanner()

    def choose(self, *, strategy: Strategy, failure_code: str, allowed_actions: list[str], context: dict) -> RecoveryDecision:
        if strategy != "recovery_agent":
            return self.fallback.choose(strategy=strategy, failure_code=failure_code, allowed_actions=allowed_actions, context=context)
        try:
            response = self.client.chat.completions.create(
                model=self.model, temperature=0, response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "你是受控售后恢复规划器。只能从 allowed_actions 选择 action；不能承诺资金、库存或审核结果；只返回 JSON: {action, reason}。"},
                    {"role": "user", "content": json.dumps({"failure_code": failure_code, "allowed_actions": allowed_actions, "context": context}, ensure_ascii=False)},
                ],
            )
            payload = json.loads(response.choices[0].message.content or "{}")
            action = payload.get("action")
            if action in allowed_actions:
                return RecoveryDecision(action, str(payload.get("reason") or "模型选择的允许恢复动作。")[:300])
        except Exception:
            pass
        return self.fallback.choose(strategy=strategy, failure_code=failure_code, allowed_actions=allowed_actions, context=context)
