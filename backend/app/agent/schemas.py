from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


IntentName = Literal["query_order", "query_logistics", "query_fulfillment", "check_eligibility", "create_after_sales", "request_manual_review", "schedule_pickup", "knowledge_qa", "unknown"]


class IntentDecision(BaseModel):
    intent: IntentName
    order_id: str | None = None
    case_id: int | None = None
    request_type: Literal["refund", "exchange"] | None = None
    reason: str | None = None
    time_slot: str | None = None

    model_config = {"extra": "forbid"}


class AgentMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    message_id: str | None = Field(default=None, min_length=8, max_length=64)

    model_config = {"extra": "forbid"}


class AgentMessageResponse(BaseModel):
    thread_id: str
    run_id: str
    status: Literal["completed", "awaiting_confirmation", "failed"]
    response: str
    confirmation_id: str | None = None
    case_id: int | None = None
    ticket_id: str | None = None
    retrieval_id: str | None = None
    citations: list[str] = Field(default_factory=list)


class ConfirmationRequest(BaseModel):
    approved: bool

    model_config = {"extra": "forbid"}


class AgentToolCallResponse(BaseModel):
    sequence_no: int
    graph_node: str
    tool_name: str
    arguments_json: dict
    result_json: dict | None
    m1_request_id: str
    latency_ms: int
    status: str
    error_code: str | None

    model_config = {"from_attributes": True}
