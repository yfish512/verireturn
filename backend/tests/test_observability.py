from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.domain.observability import (
    add_run_feedback, claim_one_evaluation_run, create_evaluation_run, list_alerts,
    list_evaluation_results, process_one_metric_job, publish_alert_rule_version,
    queue_metric_job, trace_projection, transition_alert,
)
from backend.app.domain.service import DomainError, create_case
from backend.app.models import AgentRun, AgentToolCall, AlertRuleVersion, MetricSnapshot, OpsAlert, OpsAlertEvent
from backend.app.schemas import AfterSalesCreateRequest
from backend.app.seed import seed_demo_data


def session_for(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm6.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    seed_demo_data(db)
    return db


def test_feedback_is_owned_once_and_does_not_mutate_run(tmp_path):
    db = session_for(tmp_path)
    run = AgentRun(id="m6-run-feedback", thread_id="m6", actor_id="U001", status="completed", model_name="test", input_text="question")
    db.add(run); db.commit()
    feedback = add_run_feedback(db, run.id, "U001", "unhelpful", "wrong_intent", "wrong route")
    assert feedback.rating == "unhelpful"
    assert db.get(AgentRun, run.id).status == "completed"
    assert add_run_feedback(db, run.id, "U001", "unhelpful", "wrong_intent", "wrong route").id == feedback.id
    try:
        add_run_feedback(db, run.id, "U002", "helpful", None, None)
    except DomainError as exc:
        assert exc.code == "AGENT_RUN_NOT_FOUND"
    else:
        raise AssertionError("other user must not rate a run")


def test_metric_worker_snapshots_alerts_and_versioned_triage(tmp_path):
    db = session_for(tmp_path)
    current = datetime.now(timezone.utc)
    db.add(AgentRun(id="m6-run-metric", thread_id="m6", actor_id="U001", status="completed", model_name="test", input_text="question", started_at=current - timedelta(minutes=1), finished_at=current))
    db.add(AlertRuleVersion(id="m6-test-rule", rule_key="m6-agent-runs", version=1, metric_name="agent.runs_total", comparison=">", threshold=Decimal("0"), severity="warning", status="published", created_by="OPS_MANAGER", published_at=current))
    db.commit()
    job = queue_metric_job(db, current - timedelta(minutes=5), current + timedelta(minutes=1))
    assert process_one_metric_job(db) == job.id
    assert db.scalar(select(func.count()).select_from(MetricSnapshot).where(MetricSnapshot.computation_job_id == job.id)) == 7
    alert = db.scalar(select(OpsAlert).where(OpsAlert.rule_version_id == "m6-test-rule"))
    assert alert.status == "open"
    acknowledged = transition_alert(db, alert.id, "OPS001", alert.version, "acknowledged")
    resolved = transition_alert(db, alert.id, "OPS001", acknowledged.version, "resolved", "已完成指标核查")
    assert resolved.status == "resolved"
    assert [item.event_type for item in db.scalars(select(OpsAlertEvent).where(OpsAlertEvent.alert_id == alert.id).order_by(OpsAlertEvent.sequence_no))] == ["ALERT_OPENED", "ALERT_ACKNOWLEDGED", "ALERT_RESOLVED"]


def test_trace_projection_links_agent_tool_to_case_audit(tmp_path):
    db = session_for(tmp_path)
    run = AgentRun(id="m6-run-trace", thread_id="m6", actor_id="U001", status="completed", model_name="test", input_text="request")
    db.add(run); db.commit()
    case = create_case(db, "U001", AfterSalesCreateRequest(order_id="O1001", request_type="refund", reason="trace"), "m6-trace-create", "m6-run-trace:1")
    db.add(AgentToolCall(run_id=run.id, sequence_no=1, graph_node="create_case", tool_name="create_after_sales_case", arguments_json={"order_id": "O1001"}, result_json={"id": case.id}, m1_request_id="m6-run-trace:1", latency_ms=10, status="succeeded"))
    db.commit()
    trace = trace_projection(db, run_id=run.id)
    assert [item.id for item in trace["cases"]] == [case.id]
    assert trace["audit_logs"][0].request_id == "m6-run-trace:1"


def test_evaluation_run_is_seeded_and_claimable(tmp_path):
    db = session_for(tmp_path)
    queued = create_evaluation_run(db, "OPS_MANAGER", "m5", "deepseek-v4-flash", "agent-prompt-v1")
    claimed = claim_one_evaluation_run(db)
    assert claimed is not None
    run, suite = claimed
    assert run.id == queued.id and run.status == "running" and suite.suite_key == "m5"
    try:
        create_evaluation_run(db, "OPS_MANAGER", "m5", "deepseek-v4-flash", "unregistered-prompt")
    except DomainError as exc:
        assert exc.code == "PROMPT_VERSION_NOT_SUPPORTED"
    else:
        raise AssertionError("unregistered prompt version must be rejected")


def test_alert_rule_publish_is_versioned_and_idempotent(tmp_path):
    db = session_for(tmp_path)
    first = publish_alert_rule_version(
        db, "OPS_MANAGER", rule_key="agent-feedback", metric_name="agent.negative_feedback_rate",
        comparison=">=", threshold=Decimal("0.2"), severity="warning", idempotency_key="m6-alert-rule-0001",
    )
    replay = publish_alert_rule_version(
        db, "OPS_MANAGER", rule_key="agent-feedback", metric_name="agent.negative_feedback_rate",
        comparison=">=", threshold=Decimal("0.2"), severity="warning", idempotency_key="m6-alert-rule-0001",
    )
    replacement = publish_alert_rule_version(
        db, "OPS_MANAGER", rule_key="agent-feedback", metric_name="agent.negative_feedback_rate",
        comparison=">=", threshold=Decimal("0.3"), severity="critical", idempotency_key="m6-alert-rule-0002",
    )
    assert replay.id == first.id
    assert (first.status, replacement.version, replacement.status) == ("retired", 2, "published")
    try:
        publish_alert_rule_version(
            db, "OPS_MANAGER", rule_key="agent-feedback", metric_name="agent.negative_feedback_rate",
            comparison=">=", threshold=Decimal("0.4"), severity="critical", idempotency_key="m6-alert-rule-0001",
        )
    except DomainError as exc:
        assert exc.code == "IDEMPOTENCY_KEY_CONFLICT"
    else:
        raise AssertionError("different command must not reuse idempotency key")


def test_evaluation_results_are_scoped_to_existing_run(tmp_path):
    db = session_for(tmp_path)
    run = create_evaluation_run(db, "OPS_MANAGER", "m5", "deepseek-v4-flash", "agent-prompt-v1")
    assert list_evaluation_results(db, run.id) == []
    try:
        list_evaluation_results(db, "not-a-run")
    except DomainError as exc:
        assert exc.code == "EVALUATION_RUN_NOT_FOUND"
    else:
        raise AssertionError("unknown run must not return an unscoped result list")
