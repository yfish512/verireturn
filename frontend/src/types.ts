export type TicketStatus = "open" | "claimed" | "waiting_customer" | "approved" | "rejected" | "closed" | "expired";

export interface Ticket {
  id: string; requester_id: string; order_id: string; request_type: "refund" | "exchange"; reason: string;
  trigger_code: string; priority: "normal" | "high"; status: TicketStatus; assigned_operator_id: string | null;
  due_at: string; version: number; policy_version_id: string; created_at: string; updated_at: string;
}

export interface ReviewEvent {
  sequence_no: number; event_type: string; payload: Record<string, unknown>; actor_id: string; actor_role: string;
  request_id: string | null; created_at: string;
}

export interface Metrics {
  tickets_total: number; tickets_by_status: Record<string, number>; backlog: number; sla_breached: number;
  review_approval_rate: number; manual_intervention_rate: number; automatic_case_share: number;
  p95_review_resolution_minutes: number | null;
}

export type KnowledgeAudience = "customer" | "operator" | "shared";
export type KnowledgeVersionStatus = "draft" | "indexing" | "ready" | "published" | "retired";

export interface KnowledgeVersion {
  id: string; document_id: string; version: number; status: KnowledgeVersionStatus; content_checksum: string;
  embedding_model: string; published_by: string | null; published_at: string | null; retired_at: string | null; created_at: string;
}

export interface KnowledgeDocument {
  id: string; stable_key: string; title: string; audience: KnowledgeAudience; category: string;
  created_by: string; created_at: string; updated_at: string; versions: KnowledgeVersion[];
}

export type FulfillmentIncidentStatus = "open" | "acknowledged" | "resolved";
export interface FulfillmentIncident {
  id: string; case_id: number; inbox_event_id: string | null; incident_type: string; detail: string;
  status: FulfillmentIncidentStatus; assigned_operator_id: string | null; version: number;
  resolution_note: string | null; created_at: string; updated_at: string;
}

export interface ObservabilityOverview { metrics: Record<string, number>; alerts_open: number; }
export interface MetricSnapshot { id: string; metric_name: string; window_start: string; window_end: string; dimensions: Record<string, unknown>; value: number; created_at: string; }
export interface OpsAlert { id: string; rule_version_id: string; metric_snapshot_id: string; severity: string; status: "open" | "acknowledged" | "resolved" | "muted"; assigned_operator_id: string | null; version: number; resolution_note: string | null; created_at: string; updated_at: string; }
export interface AlertRuleVersion { id: string; rule_key: string; version: number; metric_name: string; comparison: ">" | ">="; threshold: number; severity: "warning" | "critical"; status: "draft" | "published" | "retired"; created_by: string; created_at: string; published_at: string | null; }
export interface TraceProjection { run_id: string | null; case_ids: number[]; tool_calls: Array<Record<string, unknown>>; audit_events: Array<Record<string, unknown>>; review_tickets: Array<Record<string, unknown>>; knowledge_retrievals: Array<Record<string, unknown>>; fulfillment_events: Array<Record<string, unknown>>; outbox_events: Array<Record<string, unknown>>; incidents: Array<Record<string, unknown>>; }
export interface EvaluationRun { id: string; suite_id: string; suite_key: string; requested_by: string; model_name: string; prompt_version: string; status: "queued" | "running" | "succeeded" | "failed"; attempts: number; report_json: Record<string, unknown> | null; last_error: string | null; created_at: string; finished_at: string | null; }
export interface EvaluationResult { id: string; evaluation_run_id: string; case_id: string; status: string; latency_ms: number; evidence_json: Record<string, unknown>; created_at: string; }
