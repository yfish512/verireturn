from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth import ActorContext, current_actor, require_operator, require_ops_manager
from ..database import get_db
from ..domain.observability import (
    add_run_feedback, create_evaluation_run, list_alert_rule_versions, list_alerts, list_evaluation_results,
    list_evaluation_runs, list_metrics, list_run_feedback, overview, publish_alert_rule_version,
    queue_metric_job, trace_projection, transition_alert,
)
from ..domain.service import DomainError
from ..schemas import (
    AgentRunFeedbackRequest, AgentRunFeedbackResponse, AlertRulePublishRequest, AlertRuleVersionResponse,
    EvaluationResultResponse, EvaluationRunCreateRequest, EvaluationRunResponse,
    MetricJobRequest, MetricJobResponse, MetricSnapshotResponse, OpsAlertActionRequest,
    OpsAlertResponse, TraceProjectionResponse,
)

ops_observability_router = APIRouter(prefix="/ops", tags=["observability"])
agent_feedback_router = APIRouter(prefix="/agent", tags=["agent-feedback"])


def error(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


@agent_feedback_router.post("/runs/{run_id}/feedback", response_model=AgentRunFeedbackResponse, status_code=201)
def submit_run_feedback(run_id: str, body: AgentRunFeedbackRequest, actor: ActorContext = Depends(current_actor), db: Session = Depends(get_db)):
    if actor.role != "customer":
        raise HTTPException(status_code=403, detail={"code": "CUSTOMER_ROLE_REQUIRED", "message": "只有客户可以提交 Agent 反馈。"})
    try:
        return add_run_feedback(db, run_id, actor.id, body.rating, body.reason_code, body.comment)
    except DomainError as exc:
        raise error(exc) from exc


@agent_feedback_router.get("/runs/{run_id}/feedback", response_model=list[AgentRunFeedbackResponse])
def read_run_feedback(run_id: str, actor: ActorContext = Depends(current_actor), db: Session = Depends(get_db)):
    try:
        return list_run_feedback(db, run_id, actor.id, actor.role in {"operator", "ops_manager"})
    except DomainError as exc:
        raise error(exc) from exc


@ops_observability_router.get("/observability/overview")
def read_overview(_: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    return overview(db)


@ops_observability_router.get("/metrics", response_model=list[MetricSnapshotResponse])
def read_metrics(metric_name: str | None = None, _: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    return list_metrics(db, metric_name)


@ops_observability_router.post("/metrics/jobs", response_model=MetricJobResponse, status_code=202)
def create_metric_job(body: MetricJobRequest, _: ActorContext = Depends(require_ops_manager), db: Session = Depends(get_db)):
    try:
        return queue_metric_job(db, body.window_start, body.window_end)
    except DomainError as exc:
        raise error(exc) from exc


@ops_observability_router.get("/alerts", response_model=list[OpsAlertResponse])
def read_alerts(status: str | None = Query(default=None), _: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    if status and status not in {"open", "acknowledged", "resolved", "muted"}:
        raise HTTPException(status_code=422, detail={"code": "OPS_ALERT_STATUS_INVALID", "message": "告警状态无效。"})
    return list_alerts(db, status)


@ops_observability_router.get("/alert-rules", response_model=list[AlertRuleVersionResponse])
def read_alert_rules(_: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    return list_alert_rule_versions(db)


@ops_observability_router.post("/alert-rules", response_model=AlertRuleVersionResponse, status_code=201)
def publish_alert_rule(
    body: AlertRulePublishRequest, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8),
    actor: ActorContext = Depends(require_ops_manager), db: Session = Depends(get_db),
):
    try:
        return publish_alert_rule_version(
            db, actor.id, rule_key=body.rule_key, metric_name=body.metric_name,
            comparison=body.comparison, threshold=body.threshold, severity=body.severity,
            idempotency_key=idempotency_key,
        )
    except DomainError as exc:
        raise error(exc) from exc


def _alert_action(alert_id: str, body: OpsAlertActionRequest, target: str, actor: ActorContext, db: Session):
    try:
        return transition_alert(db, alert_id, actor.id, body.expected_version, target, body.resolution_note)
    except DomainError as exc:
        raise error(exc) from exc


@ops_observability_router.post("/alerts/{alert_id}/acknowledge", response_model=OpsAlertResponse)
def acknowledge_alert(alert_id: str, body: OpsAlertActionRequest, actor: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    return _alert_action(alert_id, body, "acknowledged", actor, db)


@ops_observability_router.post("/alerts/{alert_id}/resolve", response_model=OpsAlertResponse)
def resolve_alert(alert_id: str, body: OpsAlertActionRequest, actor: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    if not body.resolution_note:
        raise HTTPException(status_code=422, detail={"code": "RESOLUTION_NOTE_REQUIRED", "message": "解决告警必须填写说明。"})
    return _alert_action(alert_id, body, "resolved", actor, db)


@ops_observability_router.post("/alerts/{alert_id}/mute", response_model=OpsAlertResponse)
def mute_alert(alert_id: str, body: OpsAlertActionRequest, actor: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    if not body.resolution_note:
        raise HTTPException(status_code=422, detail={"code": "MUTE_NOTE_REQUIRED", "message": "静默告警必须填写说明。"})
    return _alert_action(alert_id, body, "muted", actor, db)


@ops_observability_router.get("/traces", response_model=TraceProjectionResponse)
def read_trace(run_id: str | None = None, case_id: int | None = None, _: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    try:
        result = trace_projection(db, run_id=run_id, case_id=case_id)
        return {
            "run_id": result["run"].id if result["run"] else None,
            "case_ids": [item.id for item in result["cases"]],
            "tool_calls": [{"tool_name": item.tool_name, "status": item.status, "request_id": item.m1_request_id, "latency_ms": item.latency_ms} for item in result["tool_calls"]],
            "audit_events": [{"event_type": item.event_type, "request_id": item.request_id, "created_at": item.created_at.isoformat()} for item in result["audit_logs"]],
            "review_tickets": [{"id": item.id, "status": item.status} for item in result["review_tickets"]],
            "knowledge_retrievals": [{"id": item.id, "route": item.route, "latency_ms": item.latency_ms} for item in result["knowledge_retrievals"]],
            "fulfillment_events": [{"event_type": item.event_type, "sequence_no": item.sequence_no} for item in result["fulfillment_events"]],
            "outbox_events": [{"id": item.id, "event_type": item.event_type, "status": item.status} for item in result["outbox_events"]],
            "incidents": [{"id": item.id, "incident_type": item.incident_type, "status": item.status} for item in result["incidents"]],
        }
    except DomainError as exc:
        raise error(exc) from exc


@ops_observability_router.post("/evaluation-runs", response_model=EvaluationRunResponse, status_code=202)
def queue_evaluation(body: EvaluationRunCreateRequest, actor: ActorContext = Depends(require_ops_manager), db: Session = Depends(get_db)):
    try:
        return _evaluation_response(db, create_evaluation_run(db, actor.id, body.suite_key, body.model_name, body.prompt_version))
    except DomainError as exc:
        raise error(exc) from exc


@ops_observability_router.get("/evaluation-runs", response_model=list[EvaluationRunResponse])
def read_evaluation_runs(_: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    return [_evaluation_response(db, run) for run in list_evaluation_runs(db)]


@ops_observability_router.get("/evaluation-runs/{run_id}/results", response_model=list[EvaluationResultResponse])
def read_evaluation_results(run_id: str, _: ActorContext = Depends(require_operator), db: Session = Depends(get_db)):
    try:
        return list_evaluation_results(db, run_id)
    except DomainError as exc:
        raise error(exc) from exc


def _evaluation_response(db: Session, run) -> dict:
    from ..models import EvaluationSuite
    suite = db.get(EvaluationSuite, run.suite_id)
    return {
        "id": run.id, "suite_id": run.suite_id, "suite_key": suite.suite_key if suite else "unknown",
        "requested_by": run.requested_by, "model_name": run.model_name, "prompt_version": run.prompt_version,
        "status": run.status, "attempts": run.attempts, "report_json": run.report_json,
        "last_error": run.last_error, "created_at": run.created_at, "finished_at": run.finished_at,
    }
