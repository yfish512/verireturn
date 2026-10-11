"""Deterministic, adapter-layer faults for Recovery experiments."""
from __future__ import annotations
from collections import defaultdict
from typing import Callable, TypeVar
from .contracts import FaultRule, RecoveryTraceEvent

T = TypeVar("T")

class FaultInjectedError(RuntimeError):
    def __init__(self, code: str, message: str, *, may_have_committed: bool = False):
        self.code = code
        self.may_have_committed = may_have_committed
        super().__init__(message)

class FaultInjector:
    """Injects only at a tool boundary; it never mutates domain result itself."""
    def __init__(self, rule: FaultRule):
        self.rule = rule
        self.calls: dict[str, int] = defaultdict(int)
        self.events: list[RecoveryTraceEvent] = []

    def _event(self, kind: str, tool: str, **detail) -> None:
        self.events.append(RecoveryTraceEvent(len(self.events) + 1, kind, tool, detail))

    def call(self, tool_name: str, operation: Callable[[], T], *, before: Callable[[], None] | None = None) -> T:
        self.calls[tool_name] += 1
        occurrence = self.calls[tool_name]
        applies = tool_name == self.rule.tool_name and occurrence == self.rule.occurrence
        if applies and self.rule.action == "temporary_503":
            self._event("fault_injected", tool_name, action=self.rule.action, occurrence=occurrence, seed=self.rule.seed)
            raise FaultInjectedError("TOOL_TEMPORARILY_UNAVAILABLE", "依赖服务暂时不可用。")
        if applies and self.rule.action == "state_change_before_call":
            if before is None:
                raise RuntimeError("FAULT_BEFORE_CALLBACK_REQUIRED")
            before()
            self._event("fault_injected", tool_name, action=self.rule.action, occurrence=occurrence, seed=self.rule.seed)
        result = operation()
        if applies and self.rule.action == "timeout_after_commit":
            self._event("fault_injected", tool_name, action=self.rule.action, occurrence=occurrence, seed=self.rule.seed)
            raise FaultInjectedError("TOOL_OUTCOME_UNKNOWN", "工具调用超时，执行结果未知。", may_have_committed=True)
        self._event("tool_succeeded", tool_name, occurrence=occurrence)
        return result
