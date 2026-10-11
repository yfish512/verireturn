from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Literal

FaultAction = Literal["temporary_503", "timeout_after_commit", "state_change_before_call"]
Strategy = Literal["baseline", "fixed_retry", "recovery_agent"]
Outcome = Literal["autonomous_success", "safe_handoff", "safe_failure", "unsafe_failure", "awaiting_reconfirmation"]

@dataclass(frozen=True)
class FaultRule:
    scenario_id: str
    tool_name: str
    action: FaultAction
    occurrence: int = 1
    seed: int = 0

@dataclass(frozen=True)
class RecoveryDecision:
    action: Literal["retry_tool", "verify_execution_status", "refresh_business_state", "offer_alternative", "handoff_to_human", "stop_safely"]
    reason: str

@dataclass
class RecoveryTraceEvent:
    sequence_no: int
    kind: str
    tool_name: str | None
    detail: dict

    def json(self) -> dict:
        return asdict(self)

@dataclass
class ScenarioResult:
    scenario_id: str
    strategy: Strategy
    outcome: Outcome
    response: str
    extra_tool_calls: int
    retries: int
    verification_calls: int
    trace: list[RecoveryTraceEvent]
    final_state: dict

    def json(self) -> dict:
        return {
            "scenario_id": self.scenario_id, "strategy": self.strategy, "outcome": self.outcome,
            "response": self.response, "extra_tool_calls": self.extra_tool_calls, "retries": self.retries,
            "verification_calls": self.verification_calls, "trace": [event.json() for event in self.trace],
            "final_state": self.final_state,
        }
