from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


RequestType = Literal["refund", "exchange"]


class EligibilityRequest(BaseModel):
    order_id: str
    request_type: RequestType
    reason: str = Field(min_length=1, max_length=500)

    model_config = {"extra": "forbid"}


class EligibilityResponse(BaseModel):
    eligible: bool
    policy_code: str
    explanation: str
    eligible_amount: Decimal | None = None


class AfterSalesItemRequest(BaseModel):
    order_item_id: str = Field(min_length=8, max_length=36)
    quantity: int = Field(ge=1, le=99)


class AfterSalesCreateRequest(EligibilityRequest):
    items: list[AfterSalesItemRequest] | None = Field(default=None, max_length=20)


class AfterSalesCaseResponse(BaseModel):
    id: int
    order_id: str
    request_type: RequestType
    status: str
    eligible_amount: Decimal
    pickup_slot: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class PickupRequest(BaseModel):
    time_slot: str = Field(min_length=3, max_length=64)


class ManualReviewRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class AuditLogResponse(BaseModel):
    id: int
    event_type: str
    detail: str
    actor_type: str
    actor_id: str | None
    request_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


ReviewAction = Literal["request_more_info", "reject", "approve_exception", "close_duplicate"]
ReviewStatus = Literal["open", "claimed", "waiting_customer", "approved", "rejected", "closed", "expired"]


class ReviewTicketCreateRequest(BaseModel):
    order_id: str
    request_type: RequestType
    reason: str = Field(min_length=3, max_length=1000)

    model_config = {"extra": "forbid"}


class ReviewTicketSupplementRequest(BaseModel):
    content: str = Field(min_length=3, max_length=2000)
    expected_version: int = Field(ge=1)

    model_config = {"extra": "forbid"}


class ReviewClaimRequest(BaseModel):
    expected_version: int = Field(ge=1)

    model_config = {"extra": "forbid"}


class ReviewDecisionRequest(BaseModel):
    action: ReviewAction
    expected_version: int = Field(ge=1)
    reason_code: str = Field(min_length=2, max_length=64)
    customer_message: str = Field(min_length=2, max_length=1000)

    model_config = {"extra": "forbid"}


class ReviewAttachmentRequest(BaseModel):
    object_ref: str = Field(min_length=3, max_length=256)
    content_hash: str = Field(min_length=32, max_length=64, pattern=r"^[a-fA-F0-9]+$")
    media_type: str = Field(min_length=3, max_length=128)
    expected_version: int = Field(ge=1)
    model_config = {"extra": "forbid"}

class ReviewAttachmentResponse(BaseModel):
    id: str; ticket_id: str; object_ref: str; content_hash: str; media_type: str; version: int; created_at: datetime
    model_config = {"from_attributes": True}


class ReviewTicketResponse(BaseModel):
    id: str
    requester_id: str
    order_id: str
    source_case_id: int | None = None
    request_type: RequestType
    reason: str
    trigger_code: str
    priority: Literal["normal", "high"]
    status: ReviewStatus
    assigned_operator_id: str | None = None
    due_at: datetime
    version: int
    policy_version_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReviewEventResponse(BaseModel):
    sequence_no: int
    event_type: str
    payload: dict
    actor_id: str
    actor_role: str
    request_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class OpsMetricsResponse(BaseModel):
    tickets_total: int
    tickets_by_status: dict[str, int]
    backlog: int
    sla_breached: int
    review_approval_rate: float
    manual_intervention_rate: float
    automatic_case_share: float
    p95_review_resolution_minutes: float | None


class PolicyVersionPublishRequest(BaseModel):
    version: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    quality_dispute_enabled: bool = True
    quality_dispute_terms: list[str] = Field(default_factory=lambda: ["质量", "故障", "损坏", "坏了", "无法使用"], min_length=1, max_length=20)
    review_sla_hours: int = Field(default=24, ge=1, le=168)

    model_config = {"extra": "forbid"}


class PolicyVersionResponse(BaseModel):
    id: str
    version: str
    status: Literal["draft", "published", "retired"]
    rules_json: dict
    checksum: str
    published_by: str | None
    published_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


KnowledgeAudience = Literal["customer", "operator", "shared"]
KnowledgeVersionStatus = Literal["draft", "indexing", "ready", "published", "retired"]


class KnowledgeDocumentCreateRequest(BaseModel):
    stable_key: str = Field(min_length=3, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    title: str = Field(min_length=2, max_length=200)
    audience: KnowledgeAudience
    category: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    content_markdown: str = Field(min_length=10, max_length=30000)

    model_config = {"extra": "forbid"}


class KnowledgeDocumentVersionCreateRequest(BaseModel):
    content_markdown: str = Field(min_length=10, max_length=30000)

    model_config = {"extra": "forbid"}


class KnowledgeDocumentResponse(BaseModel):
    id: str
    stable_key: str
    title: str
    audience: KnowledgeAudience
    category: str
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeVersionResponse(BaseModel):
    id: str
    document_id: str
    version: int
    status: KnowledgeVersionStatus
    content_checksum: str
    embedding_model: str
    published_by: str | None
    published_at: datetime | None
    retired_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeDocumentDetailResponse(KnowledgeDocumentResponse):
    versions: list[KnowledgeVersionResponse]


class KnowledgeIngestionJobResponse(BaseModel):
    id: str
    document_version_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    attempts: int
    lease_until: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    limit: int = Field(default=4, ge=1, le=10)

    model_config = {"extra": "forbid"}


class KnowledgeSearchHitResponse(BaseModel):
    chunk_id: str
    document_id: str
    document_version_id: str
    title: str
    category: str
    version: int
    content: str
    score: float


class KnowledgeSearchResponse(BaseModel):
    retrieval_id: str
    hits: list[KnowledgeSearchHitResponse]


class KnowledgeFeedbackRequest(BaseModel):
    rating: Literal["helpful", "unhelpful"]
    reason: str | None = Field(default=None, max_length=500)

    model_config = {"extra": "forbid"}


class KnowledgeFeedbackResponse(BaseModel):
    id: str
    retrieval_log_id: str
    actor_id: str
    rating: Literal["helpful", "unhelpful"]
    reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}

FulfillmentEventType = Literal[
    "pickup.collected", "return.received", "refund.processing", "refund.completed",
    "replacement.shipped", "replacement.delivered",
]


class ProviderWebhookRequest(BaseModel):
    event_id: str = Field(min_length=8, max_length=128)
    case_id: int = Field(gt=0)
    event_type: FulfillmentEventType
    sequence_no: int = Field(ge=1)
    occurred_at: datetime
    payload: dict = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class InboxEventResponse(BaseModel):
    id: str
    provider: str
    provider_event_id: str
    case_id: int
    event_type: FulfillmentEventType
    sequence_no: int
    status: Literal["received", "applied", "deferred", "rejected"]
    rejection_code: str | None
    received_at: datetime
    applied_at: datetime | None

    model_config = {"from_attributes": True}


class FulfillmentEventResponse(BaseModel):
    id: str
    case_id: int
    provider: str
    provider_event_id: str
    event_type: FulfillmentEventType
    sequence_no: int
    payload: dict
    occurred_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class CustomerNotificationResponse(BaseModel):
    id: str
    case_id: int
    channel: str
    template: str
    content: str
    status: Literal["pending", "sent", "failed"]
    created_at: datetime
    sent_at: datetime | None

    model_config = {"from_attributes": True}


class FulfillmentStatusResponse(BaseModel):
    case_id: int
    status: str
    events: list[FulfillmentEventResponse]
    notifications: list[CustomerNotificationResponse]


class FulfillmentIncidentResponse(BaseModel):
    id: str
    case_id: int
    inbox_event_id: str | None
    incident_type: str
    detail: str
    status: Literal["open", "acknowledged", "resolved"]
    assigned_operator_id: str | None
    version: int
    resolution_note: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FulfillmentIncidentActionRequest(BaseModel):
    expected_version: int = Field(ge=1)

    model_config = {"extra": "forbid"}


class FulfillmentIncidentResolveRequest(FulfillmentIncidentActionRequest):
    resolution_note: str = Field(min_length=2, max_length=1000)


class FulfillmentReplayRequest(BaseModel):
    # The event identity comes from the path; an explicit request model retains
    # a future extension point while rejecting caller-provided state changes.
    expected_status: Literal["deferred"] = "deferred"

    model_config = {"extra": "forbid"}

FeedbackRating = Literal["helpful", "unhelpful"]


class AgentRunFeedbackRequest(BaseModel):
    rating: FeedbackRating
    reason_code: str | None = Field(default=None, max_length=64)
    comment: str | None = Field(default=None, max_length=500)
    model_config = {"extra": "forbid"}


class AgentRunFeedbackResponse(BaseModel):
    id: str; run_id: str; actor_id: str; rating: FeedbackRating; reason_code: str | None; comment: str | None; created_at: datetime
    model_config = {"from_attributes": True}


class MetricSnapshotResponse(BaseModel):
    id: str; metric_name: str; window_start: datetime; window_end: datetime; dimensions: dict; value: Decimal; created_at: datetime
    model_config = {"from_attributes": True}


class MetricJobRequest(BaseModel):
    window_start: datetime
    window_end: datetime
    model_config = {"extra": "forbid"}


class MetricJobResponse(BaseModel):
    id: str; window_start: datetime; window_end: datetime; status: str; attempts: int; lease_until: datetime | None; last_error: str | None
    model_config = {"from_attributes": True}


class OpsAlertResponse(BaseModel):
    id: str; rule_version_id: str; metric_snapshot_id: str; severity: str; status: Literal["open", "acknowledged", "resolved", "muted"]; assigned_operator_id: str | None; version: int; resolution_note: str | None; created_at: datetime; updated_at: datetime
    model_config = {"from_attributes": True}


class OpsAlertActionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    resolution_note: str | None = Field(default=None, min_length=2, max_length=1000)
    model_config = {"extra": "forbid"}


class AlertRulePublishRequest(BaseModel):
    rule_key: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9._-]*$")
    metric_name: str = Field(min_length=3, max_length=96, pattern=r"^[a-z][a-z0-9._-]*$")
    comparison: Literal[">", ">="]
    threshold: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    severity: Literal["warning", "critical"]
    model_config = {"extra": "forbid"}


class AlertRuleVersionResponse(BaseModel):
    id: str; rule_key: str; version: int; metric_name: str; comparison: Literal[">", ">="]; threshold: Decimal; severity: Literal["warning", "critical"]; status: Literal["draft", "published", "retired"]; created_by: str; created_at: datetime; published_at: datetime | None
    model_config = {"from_attributes": True}


class TraceProjectionResponse(BaseModel):
    run_id: str | None
    case_ids: list[int]
    tool_calls: list[dict]
    audit_events: list[dict]
    review_tickets: list[dict]
    knowledge_retrievals: list[dict]
    fulfillment_events: list[dict]
    outbox_events: list[dict]
    incidents: list[dict]


class EvaluationRunCreateRequest(BaseModel):
    suite_key: str = Field(pattern=r"^m[2-5]$")
    model_name: str = Field(default="deepseek-v4-flash", min_length=3, max_length=128)
    prompt_version: str = Field(default="agent-prompt-v1", min_length=3, max_length=64)
    model_config = {"extra": "forbid"}


class EvaluationRunResponse(BaseModel):
    id: str; suite_id: str; suite_key: str; requested_by: str; model_name: str; prompt_version: str; status: str; attempts: int; report_json: dict | None; last_error: str | None; created_at: datetime; finished_at: datetime | None


class EvaluationResultResponse(BaseModel):
    id: str; evaluation_run_id: str; case_id: str; status: str; latency_ms: int; evidence_json: dict; created_at: datetime
    model_config = {"from_attributes": True}
