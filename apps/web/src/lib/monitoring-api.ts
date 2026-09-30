import { apiBaseUrl } from "./api-base-url.ts";

export const WINDOW_DURATIONS = ["5m", "15m", "1h", "6h", "24h"] as const;
export const TRACE_STATUSES = ["unset", "running", "success", "error"] as const;
export const SNAPSHOT_STATUSES = ["pending", "queued", "running", "completed", "failed"] as const;
export const DRIFT_METRICS = ["trace_failure_rate", "trace_success_rate", "evaluation_pass_rate", "evaluation_error_rate", "mean_duration_ms", "p95_duration_ms", "mean_total_tokens", "trace_count"] as const;
export const DRIFT_CLASSIFICATIONS = ["drift_detected", "no_drift_detected", "insufficient_evidence"] as const;
export const CHECK_STATUSES = ["pending", "queued", "running", "completed", "failed", "skipped"] as const;
export const INCIDENT_STATUSES = ["open", "acknowledged", "resolved"] as const;
export const INCIDENT_EVENT_TYPES = ["incident_opened", "drift_reoccurred", "incident_acknowledged", "incident_resolved"] as const;

export type WindowDuration = (typeof WINDOW_DURATIONS)[number];
export type TraceStatus = (typeof TRACE_STATUSES)[number];
export type MonitoringSnapshotStatus = (typeof SNAPSHOT_STATUSES)[number];
export type DriftMetric = (typeof DRIFT_METRICS)[number];
export type DriftDirection = "increase" | "decrease";
export type DriftThresholdType = "absolute" | "relative";
export type DriftClassification = (typeof DRIFT_CLASSIFICATIONS)[number];
export type AutomaticDriftCheckStatus = (typeof CHECK_STATUSES)[number];
export type MonitoringIncidentStatus = (typeof INCIDENT_STATUSES)[number];
export type Page<T> = { items: T[]; has_more: boolean };
export type MonitoringTraceScope = { trace_name: string | null; trace_status: TraceStatus | null };

export type MonitoringDefinition = {
  id: string; name: string; description: string | null; is_enabled: boolean;
  window_duration: WindowDuration; trace_scope: MonitoringTraceScope;
  evaluation_definition_id: string | null; created_at: string; updated_at: string;
};
export type MonitoringSnapshot = {
  id: string; monitoring_definition_id: string; status: MonitoringSnapshotStatus;
  window_start: string; window_end: string; trace_count: number; successful_trace_count: number;
  failed_trace_count: number; success_rate: number | null; failure_rate: number | null;
  duration_sample_count: number; mean_duration_ms: number | null; median_duration_ms: number | null;
  p95_duration_ms: number | null; token_sample_count: number; input_tokens: number | null;
  output_tokens: number | null; total_tokens: number | null; mean_total_tokens: number | null;
  evaluated_result_count: number; passed_evaluation_count: number; failed_evaluation_count: number;
  evaluator_error_count: number; valid_binary_evaluation_count: number;
  evaluation_pass_rate: number | null; evaluation_error_rate: number | null;
  attempt_count: number; error_message: string | null; created_at: string; queued_at: string | null;
  started_at: string | null; completed_at: string | null;
};
export type DriftPolicyRuleCreate = {
  metric: DriftMetric; direction: DriftDirection; threshold_type: DriftThresholdType;
  practical_threshold: number; minimum_baseline_samples: number; minimum_current_samples: number;
};
export type DriftPolicyRule = DriftPolicyRuleCreate & { position: number };
export type DriftPolicy = { id: string; name: string; description: string | null; rules: DriftPolicyRule[]; created_at: string };
export type DriftFinding = DriftPolicyRuleCreate & {
  rule_position: number; baseline_sample_count: number; current_sample_count: number;
  baseline_value: number | null; current_value: number | null; absolute_delta: number | null;
  relative_delta: number | null; classification: DriftClassification; z_statistic: number | null;
  p_value: number | null;
};
export type DriftComparisonSummary = {
  id: string; monitoring_definition_id: string; drift_policy_id: string; baseline_snapshot_id: string;
  current_snapshot_id: string; classification: DriftClassification; policy_name: string;
  policy_description: string | null; created_at: string;
};
export type DriftComparison = DriftComparisonSummary & { findings: DriftFinding[] };
export type AutomaticDriftConfiguration = {
  id: string; name: string; monitoring_definition_id: string; drift_policy_id: string;
  is_enabled: boolean; baseline_strategy: "previous_window"; cooldown_seconds: number;
  resolve_after_clean_windows: number; created_at: string; updated_at: string;
};
export type AutomaticDriftCheck = {
  id: string; automatic_drift_configuration_id: string; baseline_snapshot_id: string | null;
  current_snapshot_id: string; drift_comparison_id: string | null; status: AutomaticDriftCheckStatus;
  skip_reason: string | null; failure_reason: string | null; error_message: string | null;
  attempt_count: number; event_suppressed: boolean; event_suppression_reason: string | null;
  created_at: string; queued_at: string | null; started_at: string | null; completed_at: string | null;
};
export type MonitoringIncident = {
  id: string; automatic_drift_configuration_id: string; monitoring_definition_id: string;
  drift_policy_id: string; status: MonitoringIncidentStatus; opened_at: string;
  acknowledged_at: string | null; resolved_at: string | null; first_drift_comparison_id: string;
  latest_drift_comparison_id: string; resolving_comparison_id: string | null;
  latest_classification: string; occurrence_count: number; consecutive_clean_count: number;
  created_at: string; updated_at: string;
};
export type MonitoringIncidentEvent = {
  id: string; incident_id: string; automatic_drift_check_id: string | null;
  drift_comparison_id: string | null; event_type: (typeof INCIDENT_EVENT_TYPES)[number]; created_at: string;
};

export class MonitoringApiError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function listMonitoringDefinitions(signal?: AbortSignal) { return parsePage(await request("/api/v1/monitoring-definitions?page_size=100", { signal }), parseMonitoringDefinition); }
export async function getMonitoringDefinition(id: string, signal?: AbortSignal) { return parseMonitoringDefinition(await request(`/api/v1/monitoring-definitions/${encodeURIComponent(id)}`, { signal })); }
export async function createMonitoringDefinition(payload: object) { return parseMonitoringDefinition(await request("/api/v1/monitoring-definitions", { method: "POST", body: payload })); }
export async function updateMonitoringDefinition(id: string, isEnabled: boolean) { return parseMonitoringDefinition(await request(`/api/v1/monitoring-definitions/${encodeURIComponent(id)}`, { method: "PATCH", body: { is_enabled: isEnabled } })); }
export async function listMonitoringSnapshots(id: string, signal?: AbortSignal) { return parsePage(await request(`/api/v1/monitoring-definitions/${encodeURIComponent(id)}/snapshots?page_size=100`, { signal }), parseMonitoringSnapshot); }
export async function materializeMonitoringSnapshot(id: string, window?: { window_start: string; window_end: string }) { return parseSnapshotAccepted(await request(`/api/v1/monitoring-definitions/${encodeURIComponent(id)}/snapshots`, { method: "POST", body: window ?? {} })); }
export async function listDriftPolicies(signal?: AbortSignal) { return parsePage(await request("/api/v1/drift-policies?page_size=100", { signal }), parseDriftPolicy); }
export async function createDriftPolicy(payload: object) { return parseDriftPolicy(await request("/api/v1/drift-policies", { method: "POST", body: payload })); }
export async function listDriftComparisons(monitorId: string, signal?: AbortSignal) { const query = new URLSearchParams({ page_size: "100", monitoring_definition_id: monitorId }); return parsePage(await request(`/api/v1/drift-comparisons?${query}`, { signal }), parseDriftComparisonSummary); }
export async function getDriftComparison(id: string, signal?: AbortSignal) { return parseDriftComparison(await request(`/api/v1/drift-comparisons/${encodeURIComponent(id)}`, { signal })); }
export async function createDriftComparison(payload: object) { return parseDriftComparison(await request("/api/v1/drift-comparisons", { method: "POST", body: payload })); }
export async function listAutomaticConfigurations(monitorId: string, signal?: AbortSignal) { const query = new URLSearchParams({ page_size: "100", monitoring_definition_id: monitorId }); return parsePage(await request(`/api/v1/automatic-drift-configurations?${query}`, { signal }), parseAutomaticDriftConfiguration); }
export async function createAutomaticConfiguration(payload: object) { return parseAutomaticDriftConfiguration(await request("/api/v1/automatic-drift-configurations", { method: "POST", body: payload })); }
export async function updateAutomaticConfiguration(id: string, isEnabled: boolean) { return parseAutomaticDriftConfiguration(await request(`/api/v1/automatic-drift-configurations/${encodeURIComponent(id)}`, { method: "PATCH", body: { is_enabled: isEnabled } })); }
export async function listAutomaticChecks(configurationId: string, signal?: AbortSignal) { const query = new URLSearchParams({ page_size: "100", automatic_drift_configuration_id: configurationId }); return parsePage(await request(`/api/v1/automatic-drift-checks?${query}`, { signal }), parseAutomaticDriftCheck); }
export async function listMonitoringIncidents(options: { monitorId?: string; configurationId?: string; status?: MonitoringIncidentStatus; signal?: AbortSignal } = {}) { const query = new URLSearchParams({ page_size: "100" }); if (options.monitorId) query.set("monitoring_definition_id", options.monitorId); if (options.configurationId) query.set("automatic_drift_configuration_id", options.configurationId); if (options.status) query.set("status", options.status); return parsePage(await request(`/api/v1/monitoring-incidents?${query}`, { signal: options.signal }), parseMonitoringIncident); }
export async function getMonitoringIncident(id: string, signal?: AbortSignal) { return parseMonitoringIncident(await request(`/api/v1/monitoring-incidents/${encodeURIComponent(id)}`, { signal })); }
export async function acknowledgeIncident(id: string) { return parseMonitoringIncident(await request(`/api/v1/monitoring-incidents/${encodeURIComponent(id)}/acknowledge`, { method: "POST" })); }
export async function listIncidentEvents(id: string, signal?: AbortSignal) { return parsePage(await request(`/api/v1/monitoring-incidents/${encodeURIComponent(id)}/events?page_size=100`, { signal }), parseMonitoringIncidentEvent); }

export function parseMonitoringDefinition(value: unknown): MonitoringDefinition { const item = record(value); return { id: text(item.id), name: text(item.name), description: nullableText(item.description), is_enabled: bool(item.is_enabled), window_duration: enumeration(item.window_duration, WINDOW_DURATIONS), trace_scope: parseTraceScope(item.trace_scope), evaluation_definition_id: nullableText(item.evaluation_definition_id), created_at: text(item.created_at), updated_at: text(item.updated_at) }; }
export function parseMonitoringSnapshot(value: unknown): MonitoringSnapshot { const item = record(value); return { id: text(item.id), monitoring_definition_id: text(item.monitoring_definition_id), status: enumeration(item.status, SNAPSHOT_STATUSES), window_start: text(item.window_start), window_end: text(item.window_end), trace_count: integer(item.trace_count), successful_trace_count: integer(item.successful_trace_count), failed_trace_count: integer(item.failed_trace_count), success_rate: nullableNumber(item.success_rate), failure_rate: nullableNumber(item.failure_rate), duration_sample_count: integer(item.duration_sample_count), mean_duration_ms: nullableNumber(item.mean_duration_ms), median_duration_ms: nullableNumber(item.median_duration_ms), p95_duration_ms: nullableNumber(item.p95_duration_ms), token_sample_count: integer(item.token_sample_count), input_tokens: nullableInteger(item.input_tokens), output_tokens: nullableInteger(item.output_tokens), total_tokens: nullableInteger(item.total_tokens), mean_total_tokens: nullableNumber(item.mean_total_tokens), evaluated_result_count: integer(item.evaluated_result_count), passed_evaluation_count: integer(item.passed_evaluation_count), failed_evaluation_count: integer(item.failed_evaluation_count), evaluator_error_count: integer(item.evaluator_error_count), valid_binary_evaluation_count: integer(item.valid_binary_evaluation_count), evaluation_pass_rate: nullableNumber(item.evaluation_pass_rate), evaluation_error_rate: nullableNumber(item.evaluation_error_rate), attempt_count: integer(item.attempt_count), error_message: nullableText(item.error_message), created_at: text(item.created_at), queued_at: nullableText(item.queued_at), started_at: nullableText(item.started_at), completed_at: nullableText(item.completed_at) }; }
export function parseDriftComparison(value: unknown): DriftComparison { const item = record(value); return { ...parseDriftComparisonSummary(item), findings: array(item.findings).map(parseDriftFinding) }; }
export function parseAutomaticDriftCheck(value: unknown): AutomaticDriftCheck { const item = record(value); return { id: text(item.id), automatic_drift_configuration_id: text(item.automatic_drift_configuration_id), baseline_snapshot_id: nullableText(item.baseline_snapshot_id), current_snapshot_id: text(item.current_snapshot_id), drift_comparison_id: nullableText(item.drift_comparison_id), status: enumeration(item.status, CHECK_STATUSES), skip_reason: nullableText(item.skip_reason), failure_reason: nullableText(item.failure_reason), error_message: nullableText(item.error_message), attempt_count: integer(item.attempt_count), event_suppressed: bool(item.event_suppressed), event_suppression_reason: nullableText(item.event_suppression_reason), created_at: text(item.created_at), queued_at: nullableText(item.queued_at), started_at: nullableText(item.started_at), completed_at: nullableText(item.completed_at) }; }
export function parseMonitoringIncident(value: unknown): MonitoringIncident { const item = record(value); return { id: text(item.id), automatic_drift_configuration_id: text(item.automatic_drift_configuration_id), monitoring_definition_id: text(item.monitoring_definition_id), drift_policy_id: text(item.drift_policy_id), status: enumeration(item.status, INCIDENT_STATUSES), opened_at: text(item.opened_at), acknowledged_at: nullableText(item.acknowledged_at), resolved_at: nullableText(item.resolved_at), first_drift_comparison_id: text(item.first_drift_comparison_id), latest_drift_comparison_id: text(item.latest_drift_comparison_id), resolving_comparison_id: nullableText(item.resolving_comparison_id), latest_classification: text(item.latest_classification), occurrence_count: integer(item.occurrence_count), consecutive_clean_count: integer(item.consecutive_clean_count), created_at: text(item.created_at), updated_at: text(item.updated_at) }; }

function parseTraceScope(value: unknown): MonitoringTraceScope { const item = record(value); return { trace_name: nullableText(item.trace_name), trace_status: nullableEnum(item.trace_status, TRACE_STATUSES) }; }
function parseDriftPolicyRule(value: unknown): DriftPolicyRule { const item = record(value); return { position: integer(item.position), metric: enumeration(item.metric, DRIFT_METRICS), direction: enumeration(item.direction, ["increase", "decrease"] as const), threshold_type: enumeration(item.threshold_type, ["absolute", "relative"] as const), practical_threshold: number(item.practical_threshold), minimum_baseline_samples: integer(item.minimum_baseline_samples), minimum_current_samples: integer(item.minimum_current_samples) }; }
function parseDriftPolicy(value: unknown): DriftPolicy { const item = record(value); return { id: text(item.id), name: text(item.name), description: nullableText(item.description), rules: array(item.rules).map(parseDriftPolicyRule), created_at: text(item.created_at) }; }
function parseDriftComparisonSummary(value: unknown): DriftComparisonSummary { const item = record(value); return { id: text(item.id), monitoring_definition_id: text(item.monitoring_definition_id), drift_policy_id: text(item.drift_policy_id), baseline_snapshot_id: text(item.baseline_snapshot_id), current_snapshot_id: text(item.current_snapshot_id), classification: enumeration(item.classification, DRIFT_CLASSIFICATIONS), policy_name: text(item.policy_name), policy_description: nullableText(item.policy_description), created_at: text(item.created_at) }; }
function parseDriftFinding(value: unknown): DriftFinding { const item = record(value); return { rule_position: integer(item.rule_position), metric: enumeration(item.metric, DRIFT_METRICS), direction: enumeration(item.direction, ["increase", "decrease"] as const), threshold_type: enumeration(item.threshold_type, ["absolute", "relative"] as const), practical_threshold: number(item.practical_threshold), minimum_baseline_samples: integer(item.minimum_baseline_samples), minimum_current_samples: integer(item.minimum_current_samples), baseline_sample_count: integer(item.baseline_sample_count), current_sample_count: integer(item.current_sample_count), baseline_value: nullableNumber(item.baseline_value), current_value: nullableNumber(item.current_value), absolute_delta: nullableNumber(item.absolute_delta), relative_delta: nullableNumber(item.relative_delta), classification: enumeration(item.classification, DRIFT_CLASSIFICATIONS), z_statistic: nullableNumber(item.z_statistic), p_value: nullableNumber(item.p_value) }; }
function parseAutomaticDriftConfiguration(value: unknown): AutomaticDriftConfiguration { const item = record(value); return { id: text(item.id), name: text(item.name), monitoring_definition_id: text(item.monitoring_definition_id), drift_policy_id: text(item.drift_policy_id), is_enabled: bool(item.is_enabled), baseline_strategy: literal(item.baseline_strategy, "previous_window"), cooldown_seconds: integer(item.cooldown_seconds), resolve_after_clean_windows: integer(item.resolve_after_clean_windows), created_at: text(item.created_at), updated_at: text(item.updated_at) }; }
function parseMonitoringIncidentEvent(value: unknown): MonitoringIncidentEvent { const item = record(value); return { id: text(item.id), incident_id: text(item.incident_id), automatic_drift_check_id: nullableText(item.automatic_drift_check_id), drift_comparison_id: nullableText(item.drift_comparison_id), event_type: enumeration(item.event_type, INCIDENT_EVENT_TYPES), created_at: text(item.created_at) }; }
function parseSnapshotAccepted(value: unknown) { const item = record(value); return { snapshot_id: text(item.snapshot_id), status: enumeration(item.status, SNAPSHOT_STATUSES), queue_delivery: enumeration(item.queue_delivery, ["enqueued", "deferred", "not_required"] as const), created: bool(item.created) }; }

async function request(path: string, options: { method?: "GET" | "POST" | "PATCH"; body?: object; signal?: AbortSignal } = {}) {
  const response = await fetch(`${apiBaseUrl}${path}`, { method: options.method ?? "GET", signal: options.signal, headers: { Accept: "application/json", ...(options.body === undefined ? {} : { "Content-Type": "application/json" }) }, body: options.body === undefined ? undefined : JSON.stringify(options.body) });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) { const error = isRecord(data) && isRecord(data.error) ? data.error : isRecord(data) && isRecord(data.detail) ? data.detail : null; throw new MonitoringApiError(error && typeof error.message === "string" ? error.message : "The monitoring request failed.", response.status, error && typeof error.code === "string" ? error.code : undefined); }
  return data;
}
function parsePage<T>(value: unknown, parser: (value: unknown) => T): Page<T> { const item = record(value); return { items: array(item.items).map(parser), has_more: bool(item.has_more) }; }
function isRecord(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === "object" && !Array.isArray(value); }
function record(value: unknown): Record<string, unknown> { if (!isRecord(value)) invalid(); return value; }
function array(value: unknown): unknown[] { if (!Array.isArray(value)) invalid(); return value; }
function text(value: unknown): string { if (typeof value !== "string") invalid(); return value; }
function nullableText(value: unknown): string | null { return value === null ? null : text(value); }
function integer(value: unknown): number { if (!Number.isInteger(value)) invalid(); return value as number; }
function nullableInteger(value: unknown): number | null { return value === null ? null : integer(value); }
function number(value: unknown): number { if (typeof value !== "number" || !Number.isFinite(value)) invalid(); return value; }
function nullableNumber(value: unknown): number | null { return value === null ? null : number(value); }
function bool(value: unknown): boolean { if (typeof value !== "boolean") invalid(); return value; }
function enumeration<const T extends readonly string[]>(value: unknown, allowed: T): T[number] { const parsed = text(value); if (!allowed.includes(parsed)) invalid(); return parsed as T[number]; }
function nullableEnum<const T extends readonly string[]>(value: unknown, allowed: T): T[number] | null { return value === null ? null : enumeration(value, allowed); }
function literal<const T extends string>(value: unknown, expected: T): T { if (value !== expected) invalid(); return expected; }
function invalid(): never { throw new MonitoringApiError("The monitoring service returned an unexpected response.", 502, "INVALID_RESPONSE"); }
