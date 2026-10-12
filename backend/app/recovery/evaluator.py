"""Comparison evaluator for baseline, fixed retry and recovery-agent policies."""
from __future__ import annotations
import os
import time
from collections import Counter, defaultdict
from typing import Iterable
from .contracts import Strategy
from .graph import RecoveryGraph
from .planner import BoundedRecoveryPlanner, OpenAIRecoveryPlanner, RecoveryPlanner
from .scenarios import SCENARIOS, ScenarioHarness

STRATEGIES: tuple[Strategy, ...] = ("baseline", "fixed_retry", "recovery_agent")

def planner_from_env(mode: str = "bounded", *, model_name: str | None = None) -> RecoveryPlanner:
    if mode == "llm":
        base_url, api_key = os.getenv("LLM_BASE_URL"), os.getenv("LLM_API_KEY")
        if base_url and api_key:
            return OpenAIRecoveryPlanner(base_url, api_key, model_name or os.getenv("LLM_MODEL", "deepseek-v4-flash"))
    return BoundedRecoveryPlanner()

def _aggregate(rows: list[dict]) -> dict:
    totals = Counter(item["outcome"] for item in rows)
    count = len(rows) or 1
    return {
        "tasks": len(rows),
        "autonomous_success_rate": round(totals["autonomous_success"] / count, 4),
        "safe_handoff_rate": round(totals["safe_handoff"] / count, 4),
        "safe_failure_rate": round(totals["safe_failure"] / count, 4),
        "unsafe_failure_rate": round(totals["unsafe_failure"] / count, 4),
        "awaiting_reconfirmation_rate": round(totals["awaiting_reconfirmation"] / count, 4),
        "average_extra_tool_calls": round(sum(item["extra_tool_calls"] for item in rows) / count, 2),
        "average_retries": round(sum(item["retries"] for item in rows) / count, 2),
        "average_verification_calls": round(sum(item["verification_calls"] for item in rows) / count, 2),
    }

def evaluate(cases: Iterable[dict], *, planner_mode: str = "bounded", model_name: str | None = None) -> dict:
    """Run every policy against a fresh seeded DB for each task.

    Fresh fixtures prevent an earlier strategy from consuming stock or changing
    payment state for later strategies; this is essential for a fair comparison.
    """
    cases = list(cases)
    if cases and cases[0].get("schema_version") == 2:
        from .evaluate_v2 import evaluate_v2
        return evaluate_v2(cases, planner_mode=planner_mode, model_name=model_name)
    if planner_mode == "llm":
        raise ValueError("Live evaluation requires the v2 corpus and budgeted planner")
    rows: list[dict] = []
    errors: list[str] = []
    planner = planner_from_env(planner_mode, model_name=model_name)
    for case in cases:
        scenario_id = case["scenario_id"]
        if scenario_id not in SCENARIOS:
            raise ValueError(f"unknown recovery scenario: {scenario_id}")
        seed = int(case.get("seed", 0))
        for strategy in STRATEGIES:
            started = time.perf_counter()
            result_id = f"{case.get('id', scenario_id)}:{strategy}"
            try:
                result = RecoveryGraph(ScenarioHarness(scenario_id, seed), strategy, planner).run()
                item = {"id": result_id, "status": result.outcome, "duration_ms": int((time.perf_counter() - started) * 1000), **result.json()}
                rows.append(item)
            except Exception as error:
                errors.append(f"{result_id}: {error}")
                rows.append({"id": result_id, "status": "harness_error", "duration_ms": int((time.perf_counter() - started) * 1000), "error": str(error)})
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in rows:
        if item["status"] != "harness_error": grouped[item["strategy"]].append(item)
    return {
        "suite": "recovery", "planner_mode": planner_mode,
        "episodes": len(cases),
        "scenario_families": sorted({item.get("scenario_id") for item in cases}),
        "total": len(rows), "failed": len(errors), "errors": errors, "results": rows,
        "metrics_by_strategy": {strategy: _aggregate(grouped[strategy]) for strategy in STRATEGIES},
    }

def default_cases() -> list[dict]:
    return [{"id": f"{scenario_id}-{seed}", "scenario_id": scenario_id, "seed": seed} for scenario_id in SCENARIOS for seed in (101, 202, 303)]
