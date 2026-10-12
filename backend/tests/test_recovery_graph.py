from backend.app.recovery.graph import RecoveryGraph
from backend.app.recovery.scenarios import ScenarioHarness


def test_recovery_evaluation_corpus_has_300_balanced_seeded_episodes():
    import json
    from collections import Counter
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "evals/cases/recovery_agent_cases.jsonl"
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(cases) == 300
    assert Counter(item["scenario_id"] for item in cases) == {
        "inventory_temporarily_unavailable": 100,
        "refund_outcome_unknown": 100,
        "inventory_changed_during_confirmation": 100,
    }
    assert len({item["seed"] for item in cases}) == 300


def run(scenario, strategy):
    return RecoveryGraph(ScenarioHarness(scenario, seed=20261012), strategy).run()


def test_temporary_inventory_failure_is_retried_only_by_retry_strategies():
    baseline = run("inventory_temporarily_unavailable", "baseline")
    fixed = run("inventory_temporarily_unavailable", "fixed_retry")
    recovery = run("inventory_temporarily_unavailable", "recovery_agent")
    assert baseline.outcome == "safe_failure"
    assert fixed.outcome == recovery.outcome == "autonomous_success"
    assert fixed.retries == recovery.retries == 1
    assert recovery.final_state["available"] >= 0


def test_unknown_refund_outcome_is_verified_not_blindly_retried():
    baseline = run("refund_outcome_unknown", "baseline")
    fixed = run("refund_outcome_unknown", "fixed_retry")
    recovery = run("refund_outcome_unknown", "recovery_agent")
    assert baseline.outcome == "safe_failure"
    assert fixed.outcome == "unsafe_failure"
    assert fixed.final_state["refund_attempts"] == 2
    assert recovery.outcome == "autonomous_success"
    assert recovery.verification_calls == 1
    assert recovery.retries == 0
    assert recovery.final_state["refund_attempts"] == 1


def test_inventory_change_requires_a_new_customer_choice():
    baseline = run("inventory_changed_during_confirmation", "baseline")
    fixed = run("inventory_changed_during_confirmation", "fixed_retry")
    recovery = run("inventory_changed_during_confirmation", "recovery_agent")
    assert baseline.outcome == fixed.outcome == "safe_failure"
    assert recovery.outcome == "awaiting_reconfirmation"
    assert recovery.verification_calls == 1
    assert recovery.final_state["case_status"] == "fulfillment_exception"
    assert "再次确认" in recovery.response


def test_comparison_report_keeps_unsafe_retry_separate_from_safe_handoff():
    from backend.app.recovery.evaluator import evaluate
    report = evaluate([
        {"id": "temporary", "scenario_id": "inventory_temporarily_unavailable", "seed": 1},
        {"id": "unknown", "scenario_id": "refund_outcome_unknown", "seed": 2},
        {"id": "changed", "scenario_id": "inventory_changed_during_confirmation", "seed": 3},
    ])
    assert report["failed"] == 0
    fixed = report["metrics_by_strategy"]["fixed_retry"]
    recovery = report["metrics_by_strategy"]["recovery_agent"]
    assert fixed["unsafe_failure_rate"] > 0
    assert recovery["autonomous_success_rate"] > fixed["autonomous_success_rate"]
    assert recovery["awaiting_reconfirmation_rate"] > 0


def test_recovery_suite_is_queued_with_its_own_prompt_contract(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.app.database import Base
    from backend.app.domain.observability import create_evaluation_run
    from backend.app.seed import seed_demo_data
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery-eval.db'}"); Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        seed_demo_data(db)
        run = create_evaluation_run(db, "OPS_MANAGER", "recovery", "deepseek-v4-flash", "recovery-prompt-v1")
        assert run.status == "queued"


def test_recovery_outcomes_fit_the_persisted_evaluation_contract(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.app.database import Base
    from backend.app.domain.observability import finish_evaluation_run
    from backend.app.models import EvaluationResult
    from backend.app.seed import seed_demo_data
    from backend.app.domain.observability import create_evaluation_run
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery-result.db'}"); Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        seed_demo_data(db)
        run = create_evaluation_run(db, "OPS_MANAGER", "recovery", "deepseek-v4-flash", "recovery-prompt-v1")
        finish_evaluation_run(db, run.id, {"failed": 0, "results": [{"id": "recovery-case", "status": "awaiting_reconfirmation", "duration_ms": 1}]})
        assert db.query(EvaluationResult).one().status == "awaiting_reconfirmation"
