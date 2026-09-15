import type { AgentMessage, AgentTaskCancelResult, AgentTaskMemory, AgentThreadSnapshot, AgentToolCall, AlertRuleVersion, EvaluationResult, EvaluationRun, FulfillmentIncident, FulfillmentStatus, KnowledgeDocument, KnowledgeAudience, MetricSnapshot, Metrics, ObservabilityOverview, OpsAlert, ReviewEvent, Ticket, TicketStatus, TraceProjection, PickupSlot, OrderLine } from "./types";

const actorId = () => localStorage.getItem("verireturn.opsActor") || "OPS001";
const idempotency = () => `ops-ui-${crypto.randomUUID()}`;

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", "X-Demo-User-Id": actorId(), ...(init.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail?.message || `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  tickets: (status?: TicketStatus) => request<Ticket[]>(`/ops/review-tickets${status ? `?status=${status}` : ""}`),
  metrics: () => request<Metrics>("/ops/metrics/summary"),
  timeline: (id: string) => request<ReviewEvent[]>(`/ops/review-tickets/${id}/timeline`),
  claim: (ticket: Ticket) => request<Ticket>(`/ops/review-tickets/${ticket.id}/claim`, {
    method: "POST", headers: { "Idempotency-Key": idempotency() }, body: JSON.stringify({ expected_version: ticket.version }),
  }),
  decide: (ticket: Ticket, action: string, reasonCode: string, customerMessage: string) => request<Ticket>(
    `/ops/review-tickets/${ticket.id}/decisions`, {
      method: "POST", headers: { "Idempotency-Key": idempotency() },
      body: JSON.stringify({ action, expected_version: ticket.version, reason_code: reasonCode, customer_message: customerMessage }),
    },
  ),
  knowledgeDocuments: () => request<KnowledgeDocument[]>("/ops/knowledge/documents"),
  createKnowledgeDocument: (payload: { stable_key: string; title: string; audience: KnowledgeAudience; category: string; content_markdown: string }) =>
    request<KnowledgeDocument>("/ops/knowledge/documents", { method: "POST", headers: { "Idempotency-Key": idempotency() }, body: JSON.stringify(payload) }),
  queueKnowledgeVersion: (versionId: string) =>
    request(`/ops/knowledge/versions/${versionId}/ingestion`, { method: "POST", headers: { "Idempotency-Key": idempotency() } }),
  publishKnowledgeVersion: (versionId: string) =>
    request(`/ops/knowledge/versions/${versionId}/publish`, { method: "POST", headers: { "Idempotency-Key": idempotency() } }),
  observabilityOverview: () => request<ObservabilityOverview>("/ops/observability/overview"),
  metricSnapshots: () => request<MetricSnapshot[]>("/ops/metrics"),
  opsAlerts: () => request<OpsAlert[]>("/ops/alerts"),
  acknowledgeOpsAlert: (alert: OpsAlert) => request<OpsAlert>(`/ops/alerts/${alert.id}/acknowledge`, { method: "POST", body: JSON.stringify({ expected_version: alert.version }) }),
  resolveOpsAlert: (alert: OpsAlert, resolutionNote: string) => request<OpsAlert>(`/ops/alerts/${alert.id}/resolve`, { method: "POST", body: JSON.stringify({ expected_version: alert.version, resolution_note: resolutionNote }) }),
  muteOpsAlert: (alert: OpsAlert, resolutionNote: string) => request<OpsAlert>(`/ops/alerts/${alert.id}/mute`, { method: "POST", body: JSON.stringify({ expected_version: alert.version, resolution_note: resolutionNote }) }),
  alertRules: () => request<AlertRuleVersion[]>("/ops/alert-rules"),
  publishAlertRule: (payload: { rule_key: string; metric_name: string; comparison: ">" | ">="; threshold: number; severity: "warning" | "critical" }) =>
    request<AlertRuleVersion>("/ops/alert-rules", { method: "POST", headers: { "Idempotency-Key": idempotency() }, body: JSON.stringify(payload) }),
  trace: (runId?: string, caseId?: string) => request<TraceProjection>(`/ops/traces?${new URLSearchParams({ ...(runId ? { run_id: runId } : {}), ...(caseId ? { case_id: caseId } : {}) })}`),
  evaluationRuns: () => request<EvaluationRun[]>("/ops/evaluation-runs"),
  queueEvaluation: (payload: { suite_key: string; model_name: string; prompt_version: string }) => request<EvaluationRun>("/ops/evaluation-runs", { method: "POST", body: JSON.stringify(payload) }),
  evaluationResults: (runId: string) => request<EvaluationResult[]>(`/ops/evaluation-runs/${runId}/results`),
  fulfillmentIncidents: () => request<FulfillmentIncident[]>("/ops/fulfillment/incidents"),
  acknowledgeFulfillmentIncident: (incident: FulfillmentIncident) => request<FulfillmentIncident>(`/ops/fulfillment/incidents/${incident.id}/acknowledge`, {
    method: "POST", body: JSON.stringify({ expected_version: incident.version }),
  }),
  resolveFulfillmentIncident: (incident: FulfillmentIncident, resolutionNote: string) => request<FulfillmentIncident>(`/ops/fulfillment/incidents/${incident.id}/resolve`, {
    method: "POST", body: JSON.stringify({ expected_version: incident.version, resolution_note: resolutionNote }),
  }),
  createAgentThread: (actor: string) => request<{ thread_id: string }>("/agent/threads", { method: "POST", headers: { "X-Demo-User-Id": actor } }),
  agentThread: (actor: string, threadId: string, options: { beforeSequence?: number; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (options.beforeSequence) query.set("before_sequence", String(options.beforeSequence));
    if (options.limit) query.set("limit", String(options.limit));
    const suffix = query.size ? `?${query}` : "";
    return request<AgentThreadSnapshot>(`/agent/threads/${threadId}${suffix}`, { headers: { "X-Demo-User-Id": actor } });
  },
  agentMessage: (actor: string, threadId: string, message: string, messageId: string) => request<AgentMessage>(`/agent/threads/${threadId}/messages`, {
    method: "POST", headers: { "X-Demo-User-Id": actor }, body: JSON.stringify({ message, message_id: messageId }),
  }),
  agentTasks: (actor: string, threadId: string) => request<AgentTaskMemory[]>(`/agent/threads/${threadId}/tasks`, { headers: { "X-Demo-User-Id": actor } }),
  focusAgentTask: (actor: string, threadId: string, taskId: string, restore = false) => request<{task: AgentTaskMemory}>(`/agent/threads/${threadId}/tasks/${taskId}/focus`, { method: "POST", headers: { "X-Demo-User-Id": actor }, body: JSON.stringify({ restore }) }),
  archiveAgentTask: (actor: string, threadId: string, taskId: string) => request<{task: AgentTaskMemory}>(`/agent/threads/${threadId}/tasks/${taskId}/archive`, { method: "POST", headers: { "X-Demo-User-Id": actor } }),
  cancelAgentTask: (actor: string, threadId: string) => request<AgentTaskCancelResult>(`/agent/threads/${threadId}/task/cancel`, {
    method: "POST", headers: { "X-Demo-User-Id": actor },
  }),
  resolveAgentConfirmation: (actor: string, confirmationId: string, approved: boolean) => request<AgentMessage>(`/agent/confirmations/${confirmationId}`, {
    method: "POST", headers: { "X-Demo-User-Id": actor }, body: JSON.stringify({ approved }),
  }),
  agentToolCalls: (actor: string, runId: string) => request<AgentToolCall[]>(`/agent/runs/${runId}/tool-calls`, {
    headers: { "X-Demo-User-Id": actor },
  }),
  pickupSlots: (actor: string) => request<PickupSlot[]>("/tools/pickup-slots", { headers: { "X-Demo-User-Id": actor } }),
  orderLines: (actor: string, orderId: string) => request<OrderLine[]>(`/tools/orders/${orderId}/items`, { headers: { "X-Demo-User-Id": actor } }),
  agentFulfillment: (actor: string, caseId: number) => request<FulfillmentStatus>(`/tools/after-sales/cases/${caseId}/fulfillment`, {
    headers: { "X-Demo-User-Id": actor },
  }),
  actorId,
  setActorId: (id: string) => localStorage.setItem("verireturn.opsActor", id),
};
