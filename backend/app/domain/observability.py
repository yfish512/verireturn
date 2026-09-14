"""M6 operational measurement layer; it never mutates M1-M5 business facts."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from prometheus_client import Gauge

from ..models import (
    Actor, AfterSalesCase, AgentRun, AgentRunFeedback, AgentToolCall, AlertRuleVersion,
    AuditLog, CustomerNotification, EvaluationResult, EvaluationRun, EvaluationSuite,
    FulfillmentEvent, FulfillmentIncident, IdempotencyRecord, InboxEvent, KnowledgeRetrievalLog,
    MetricComputationJob, MetricSnapshot, OpsAlert, OpsAlertEvent, OutboxDelivery,
    OutboxEvent, ReviewEvent, ReviewTicket,
)
from .service import DomainError, request_fingerprint

PROMETHEUS_METRICS = Gauge("verireturn_operational_metric", "Latest M6 aggregated operational metric", ["metric_name"])
PROMETHEUS_ALERTS = Gauge("verireturn_open_alerts", "Open or acknowledged M6 operational alerts")


def now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> str:
    return str(uuid4())


def add_run_feedback(db: Session, run_id: str, actor_id: str, rating: str, reason_code: str | None, comment: str | None) -> AgentRunFeedback:
    run = db.get(AgentRun, run_id)
    if run is None or run.actor_id != actor_id:
        raise DomainError("AGENT_RUN_NOT_FOUND", "Agent 运行记录不存在或不属于当前用户。", 404)
    existing = db.scalar(select(AgentRunFeedback).where(AgentRunFeedback.run_id == run_id, AgentRunFeedback.actor_id == actor_id))
    if existing is not None:
        if (existing.rating, existing.reason_code, existing.comment) != (rating, reason_code, comment):
            raise DomainError("AGENT_FEEDBACK_EXISTS", "同一运行只能提交一次反馈。", 409)
        return existing
    feedback = AgentRunFeedback(id=_id(), run_id=run_id, actor_id=actor_id, rating=rating, reason_code=reason_code, comment=comment)
    db.add(feedback)
    db.commit()
    return feedback


def list_run_feedback(db: Session, run_id: str, actor_id: str, is_operator: bool = False) -> list[AgentRunFeedback]:
    run = db.get(AgentRun, run_id)
    if run is None or (not is_operator and run.actor_id != actor_id):
        raise DomainError("AGENT_RUN_NOT_FOUND", "Agent 运行记录不存在或无权查看。", 404)
    return list(db.scalars(select(AgentRunFeedback).where(AgentRunFeedback.run_id == run_id).order_by(AgentRunFeedback.created_at)))


def queue_metric_job(db: Session, start: datetime, end: datetime) -> MetricComputationJob:
    if start >= end or end - start > timedelta(days=31):
        raise DomainError("METRIC_WINDOW_INVALID", "指标窗口必须有效且不超过 31 天。", 422)
    existing = db.scalar(select(MetricComputationJob).where(MetricComputationJob.window_start == start, MetricComputationJob.window_end == end))
    if existing is not None:
        return existing
    job = MetricComputationJob(id=_id(), window_start=start, window_end=end)
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.scalar(select(MetricComputationJob).where(MetricComputationJob.window_start == start, MetricComputationJob.window_end == end))
    return job


def _claim_metric_job(db: Session) -> MetricComputationJob | None:
    current = now()
    job = db.scalar(select(MetricComputationJob).where(or_(MetricComputationJob.status == "queued", (MetricComputationJob.status == "running") & (MetricComputationJob.lease_until < current))).order_by(MetricComputationJob.created_at).with_for_update(skip_locked=True).limit(1))
    if job is None:
        db.commit()
        return None
    job.status, job.attempts, job.lease_until = "running", job.attempts + 1, current + timedelta(seconds=90)
    db.commit()
    return job


def _count(db: Session, model, timestamp, start: datetime, end: datetime, *conditions) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(timestamp >= start, timestamp < end, *conditions)) or 0)


def _snapshot(db: Session, job: MetricComputationJob, name: str, value: Decimal) -> None:
    existing = db.scalar(select(MetricSnapshot).where(MetricSnapshot.metric_name == name, MetricSnapshot.window_start == job.window_start, MetricSnapshot.window_end == job.window_end, MetricSnapshot.dimension_key == "all"))
    if existing is None:
        db.add(MetricSnapshot(id=_id(), metric_name=name, window_start=job.window_start, window_end=job.window_end, dimensions={}, dimension_key="all", value=value, computation_job_id=job.id))


def process_one_metric_job(db: Session) -> str | None:
    job = _claim_metric_job(db)
    if job is None:
        return None
    try:
        start, end = job.window_start, job.window_end
        runs = _count(db, AgentRun, AgentRun.started_at, start, end)
        completed = _count(db, AgentRun, AgentRun.started_at, start, end, AgentRun.status == "completed")
        tool_total = _count(db, AgentToolCall, AgentToolCall.created_at, start, end)
        tool_ok = _count(db, AgentToolCall, AgentToolCall.created_at, start, end, AgentToolCall.status == "succeeded")
        negative = _count(db, AgentRunFeedback, AgentRunFeedback.created_at, start, end, AgentRunFeedback.rating == "unhelpful")
        feedback_total = _count(db, AgentRunFeedback, AgentRunFeedback.created_at, start, end)
        metrics = {
            "agent.runs_total": Decimal(runs),
            "agent.completion_rate": Decimal(completed) / Decimal(runs) if runs else Decimal("0"),
            "agent.tool_success_rate": Decimal(tool_ok) / Decimal(tool_total) if tool_total else Decimal("0"),
            "agent.negative_feedback_rate": Decimal(negative) / Decimal(feedback_total) if feedback_total else Decimal("0"),
            "review.backlog": Decimal(db.scalar(select(func.count()).select_from(ReviewTicket).where(ReviewTicket.status.in_(("open", "claimed", "waiting_customer")))) or 0),
            "fulfillment.outbox_pending": Decimal(db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status.in_(("pending", "processing", "dead")))) or 0),
            "fulfillment.incidents_open": Decimal(db.scalar(select(func.count()).select_from(FulfillmentIncident).where(FulfillmentIncident.status.in_(("open", "acknowledged")))) or 0),
        }
        for name, value in metrics.items():
            _snapshot(db, job, name, value)
        db.flush()
        evaluate_alerts(db, job)
        job.status, job.lease_until, job.last_error = "succeeded", None, None
        db.commit()
        return job.id
    except Exception as error:
        db.rollback()
        failed = db.get(MetricComputationJob, job.id)
        if failed:
            failed.status, failed.lease_until, failed.last_error = "failed", None, str(error)[:2000]
            db.commit()
        raise


def _event(db: Session, alert: OpsAlert, kind: str, actor_id: str, payload: dict | None = None) -> None:
    sequence = db.scalar(select(func.max(OpsAlertEvent.sequence_no)).where(OpsAlertEvent.alert_id == alert.id)) or 0
    db.add(OpsAlertEvent(id=_id(), alert_id=alert.id, sequence_no=sequence + 1, event_type=kind, payload=payload or {}, actor_id=actor_id))


def evaluate_alerts(db: Session, job: MetricComputationJob) -> list[OpsAlert]:
    rules = list(db.scalars(select(AlertRuleVersion).where(AlertRuleVersion.status == "published")))
    made: list[OpsAlert] = []
    snapshots = list(db.scalars(select(MetricSnapshot).where(MetricSnapshot.computation_job_id == job.id)))
    for rule in rules:
        for snapshot in snapshots:
            if snapshot.metric_name != rule.metric_name:
                continue
            breached = snapshot.value > rule.threshold if rule.comparison == ">" else snapshot.value >= rule.threshold
            if not breached:
                continue
            fingerprint = hashlib.sha256(f"{rule.id}:{snapshot.metric_name}:{snapshot.window_start.isoformat()}:{snapshot.dimension_key}".encode()).hexdigest()
            existing = db.scalar(select(OpsAlert).where(OpsAlert.rule_version_id == rule.id, OpsAlert.fingerprint == fingerprint))
            if existing is not None:
                continue
            alert = OpsAlert(id=_id(), rule_version_id=rule.id, metric_snapshot_id=snapshot.id, fingerprint=fingerprint, severity=rule.severity)
            db.add(alert)
            db.flush()
            _event(db, alert, "ALERT_OPENED", "system", {"metric_name": snapshot.metric_name, "value": str(snapshot.value), "threshold": str(rule.threshold)})
            made.append(alert)
    return made


def list_metrics(db: Session, name: str | None = None, limit: int = 100) -> list[MetricSnapshot]:
    stmt = select(MetricSnapshot).order_by(MetricSnapshot.window_end.desc()).limit(limit)
    if name:
        stmt = stmt.where(MetricSnapshot.metric_name == name)
    return list(db.scalars(stmt))


def list_alerts(db: Session, status: str | None = None) -> list[OpsAlert]:
    stmt = select(OpsAlert).order_by(OpsAlert.created_at.desc())
    if status:
        stmt = stmt.where(OpsAlert.status == status)
    return list(db.scalars(stmt))


def list_alert_rule_versions(db: Session) -> list[AlertRuleVersion]:
    """Return the full version history so operators can audit a threshold change."""
    return list(db.scalars(select(AlertRuleVersion).order_by(AlertRuleVersion.rule_key, AlertRuleVersion.version.desc())))


def publish_alert_rule_version(
    db: Session, manager_id: str, *, rule_key: str, metric_name: str, comparison: str,
    threshold: Decimal, severity: str, idempotency_key: str,
) -> AlertRuleVersion:
    """Atomically replace one rule's active version with an immutable new row.

    The stable manager row serializes two publishers even when they create
    different rule keys. This is deliberately conservative: alert policy
    changes are rare and correctness/auditable history matters more than
    parallel configuration throughput.
    """
    if comparison not in {">", ">="}:
        raise DomainError("ALERT_RULE_COMPARISON_INVALID", "告警比较符只能是 > 或 >=。", 422)
    if severity not in {"warning", "critical"}:
        raise DomainError("ALERT_RULE_SEVERITY_INVALID", "告警等级只能是 warning 或 critical。", 422)
    if threshold < 0:
        raise DomainError("ALERT_RULE_THRESHOLD_INVALID", "告警阈值不能为负数。", 422)
    operation = "publish_alert_rule_version"
    payload = {"rule_key": rule_key, "metric_name": metric_name, "comparison": comparison, "threshold": str(threshold), "severity": severity}
    payload_hash = request_fingerprint(payload)
    record = db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.actor_id == manager_id, IdempotencyRecord.operation == operation,
        IdempotencyRecord.idempotency_key == idempotency_key,
    ))
    if record is not None:
        if record.request_hash != payload_hash:
            raise DomainError("IDEMPOTENCY_KEY_CONFLICT", "同一幂等键不能发布不同告警规则。", 409)
        replay = db.get(AlertRuleVersion, record.resource_id)
        if replay is None:
            raise DomainError("IDEMPOTENCY_RECORD_INVALID", "幂等记录关联的告警规则不存在。", 500)
        return replay
    try:
        manager = db.scalar(select(Actor).where(Actor.id == manager_id).with_for_update())
        if manager is None or manager.role != "ops_manager" or not manager.active:
            raise DomainError("OPS_MANAGER_REQUIRED", "需要运营主管权限。", 403)
        prior = list(db.scalars(select(AlertRuleVersion).where(
            AlertRuleVersion.rule_key == rule_key, AlertRuleVersion.status == "published",
        ).with_for_update()))
        for item in prior:
            item.status = "retired"
        # A partial unique index permits one published rule. Persist retirement
        # first rather than relying on SQLAlchemy's flush ordering.
        db.flush()
        version = int(db.scalar(select(func.max(AlertRuleVersion.version)).where(AlertRuleVersion.rule_key == rule_key)) or 0) + 1
        rule = AlertRuleVersion(
            id=_id(), rule_key=rule_key, version=version, metric_name=metric_name,
            comparison=comparison, threshold=threshold, severity=severity,
            status="published", created_by=manager_id, published_at=now(),
        )
        db.add(rule)
        db.flush()
        db.add(IdempotencyRecord(
            actor_id=manager_id, operation=operation, idempotency_key=idempotency_key,
            request_hash=payload_hash, resource_type="alert_rule_version", resource_id=rule.id,
        ))
        db.commit()
        return rule
    except IntegrityError as error:
        db.rollback()
        record = db.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.actor_id == manager_id, IdempotencyRecord.operation == operation,
            IdempotencyRecord.idempotency_key == idempotency_key,
        ))
        if record is not None and record.request_hash == payload_hash:
            replay = db.get(AlertRuleVersion, record.resource_id)
            if replay is not None:
                return replay
        raise DomainError("ALERT_RULE_PUBLISH_CONFLICT", "告警规则发布冲突，请使用相同幂等键重试。", 409) from error


def transition_alert(db: Session, alert_id: str, actor_id: str, expected_version: int, target: str, note: str | None = None) -> OpsAlert:
    alert = db.scalar(select(OpsAlert).where(OpsAlert.id == alert_id).with_for_update())
    if alert is None:
        raise DomainError("OPS_ALERT_NOT_FOUND", "运营告警不存在。", 404)
    if alert.version != expected_version:
        raise DomainError("OPS_ALERT_VERSION_CONFLICT", "告警已更新，请刷新后重试。", 409)
    if target == "acknowledged" and alert.status != "open":
        raise DomainError("OPS_ALERT_NOT_OPEN", "只有开放告警可以领取。", 409)
    if target == "resolved" and alert.status not in {"open", "acknowledged"}:
        raise DomainError("OPS_ALERT_NOT_RESOLVABLE", "告警当前不能解决。", 409)
    if target == "muted" and alert.status not in {"open", "acknowledged"}:
        raise DomainError("OPS_ALERT_NOT_MUTABLE", "告警当前不能静默。", 409)
    alert.status, alert.assigned_operator_id, alert.version = target, actor_id, alert.version + 1
    if target == "resolved":
        alert.resolution_note = note
    _event(db, alert, f"ALERT_{target.upper()}", actor_id, {"note": note} if note else {})
    db.commit()
    return alert


def trace_projection(db: Session, *, run_id: str | None = None, case_id: int | None = None) -> dict:
    if not run_id and not case_id:
        raise DomainError("TRACE_QUERY_REQUIRED", "请提供 run_id 或 case_id。", 422)
    run = db.get(AgentRun, run_id) if run_id else None
    calls = list(db.scalars(select(AgentToolCall).where(AgentToolCall.run_id == run_id).order_by(AgentToolCall.sequence_no))) if run_id else []
    request_ids = [call.m1_request_id for call in calls]
    inferred_cases: set[int] = {case_id} if case_id else set()
    for call in calls:
        for payload in (call.arguments_json, call.result_json or {}):
            if isinstance(payload, dict) and isinstance(payload.get("case_id") or payload.get("id"), int):
                inferred_cases.add(payload.get("case_id") or payload["id"])
    cases = list(db.scalars(select(AfterSalesCase).where(AfterSalesCase.id.in_(inferred_cases)))) if inferred_cases else []
    ids = [case.id for case in cases]
    return {
        "run": run, "tool_calls": calls, "cases": cases,
        "audit_logs": list(db.scalars(select(AuditLog).where(or_(AuditLog.case_id.in_(ids) if ids else False, AuditLog.request_id.in_(request_ids) if request_ids else False)).order_by(AuditLog.created_at))) if (ids or request_ids) else [],
        "review_tickets": list(db.scalars(select(ReviewTicket).where(ReviewTicket.source_run_id == run_id))) if run_id else [],
        "knowledge_retrievals": list(db.scalars(select(KnowledgeRetrievalLog).where(KnowledgeRetrievalLog.agent_run_id == run_id))) if run_id else [],
        "fulfillment_events": list(db.scalars(select(FulfillmentEvent).where(FulfillmentEvent.case_id.in_(ids)).order_by(FulfillmentEvent.sequence_no))) if ids else [],
        "outbox_events": list(db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id.in_([str(i) for i in ids])))) if ids else [],
        "incidents": list(db.scalars(select(FulfillmentIncident).where(FulfillmentIncident.case_id.in_(ids)))) if ids else [],
    }


def overview(db: Session) -> dict:
    latest: dict[str, Decimal] = {}
    for snapshot in db.scalars(select(MetricSnapshot).order_by(MetricSnapshot.window_end.desc())):
        latest.setdefault(snapshot.metric_name, snapshot.value)
    metrics = {key: float(value) for key, value in latest.items()}
    alerts_open = int(db.scalar(select(func.count()).select_from(OpsAlert).where(OpsAlert.status.in_(("open", "acknowledged")))) or 0)
    for key, value in metrics.items():
        PROMETHEUS_METRICS.labels(metric_name=key).set(value)
    PROMETHEUS_ALERTS.set(alerts_open)
    return {"metrics": metrics, "alerts_open": alerts_open}


def create_evaluation_run(db: Session, manager_id: str, suite_key: str, model_name: str, prompt_version: str) -> EvaluationRun:
    suite = db.scalar(select(EvaluationSuite).where(EvaluationSuite.suite_key == suite_key).order_by(EvaluationSuite.version.desc()))
    if suite is None:
        raise DomainError("EVALUATION_SUITE_NOT_FOUND", "评测集不存在。", 404)
    if prompt_version != "agent-prompt-v1":
        raise DomainError("PROMPT_VERSION_NOT_SUPPORTED", "当前只支持已登记的 agent-prompt-v1。", 422)
    run = EvaluationRun(id=_id(), suite_id=suite.id, requested_by=manager_id, model_name=model_name, prompt_version=prompt_version)
    db.add(run)
    db.commit()
    return run


def list_evaluation_runs(db: Session, limit: int = 50) -> list[EvaluationRun]:
    return list(db.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(limit)))


def list_evaluation_results(db: Session, run_id: str) -> list[EvaluationResult]:
    if db.get(EvaluationRun, run_id) is None:
        raise DomainError("EVALUATION_RUN_NOT_FOUND", "评测运行不存在。", 404)
    return list(db.scalars(select(EvaluationResult).where(EvaluationResult.evaluation_run_id == run_id).order_by(EvaluationResult.case_id)))


def claim_one_evaluation_run(db: Session) -> tuple[EvaluationRun, EvaluationSuite] | None:
    current = now()
    run = db.scalar(select(EvaluationRun).where(or_(EvaluationRun.status == "queued", (EvaluationRun.status == "running") & (EvaluationRun.lease_until < current))).order_by(EvaluationRun.created_at).with_for_update(skip_locked=True).limit(1))
    if run is None:
        db.commit()
        return None
    suite = db.get(EvaluationSuite, run.suite_id)
    if suite is None:
        raise DomainError("EVALUATION_SUITE_NOT_FOUND", "评测集不存在。", 500)
    run.status, run.attempts, run.lease_until = "running", run.attempts + 1, current + timedelta(minutes=15)
    db.commit()
    db.refresh(run)
    db.refresh(suite)
    db.expunge(run)
    db.expunge(suite)
    return run, suite


def finish_evaluation_run(db: Session, run_id: str, report: dict | None, error: str | None = None) -> EvaluationRun:
    run = db.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id).with_for_update())
    if run is None:
        raise DomainError("EVALUATION_RUN_NOT_FOUND", "评测运行不存在。", 404)
    for item in (report or {}).get("results", []):
        case_id = str(item.get("id", "unknown"))
        existing = db.scalar(select(EvaluationResult).where(EvaluationResult.evaluation_run_id == run.id, EvaluationResult.case_id == case_id))
        if existing is None:
            db.add(EvaluationResult(id=_id(), evaluation_run_id=run.id, case_id=case_id, status=str(item.get("status", "failed")), latency_ms=int(item.get("duration_ms", 0)), evidence_json={key: value for key, value in item.items() if key != "id"}))
    failed = bool(error) or int((report or {}).get("failed", 1)) > 0
    run.status, run.lease_until, run.finished_at = ("failed" if failed else "succeeded"), None, now()
    run.report_json, run.last_error = report, error
    db.commit()
    return run
