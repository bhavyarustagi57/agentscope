import type { JsonValue } from "./trace-api";
import { apiBaseUrl } from "./api-base-url.ts";

export const EXPERIMENT_STATUSES = ["draft", "ready", "running", "completed", "failed"] as const;
export const EXPERIMENT_RUN_STATUSES = ["pending", "queued", "running", "completed", "failed"] as const;
export const CHANGE_DIRECTIONS = ["A_PASS_B_FAIL", "A_FAIL_B_PASS"] as const;

export type ExperimentStatus = (typeof EXPERIMENT_STATUSES)[number];
export type ExperimentRunStatus = (typeof EXPERIMENT_RUN_STATUSES)[number];
export type ChangeDirection = (typeof CHANGE_DIRECTIONS)[number];
export type VariantKey = "A" | "B";
export type ExperimentOutcome = "passed" | "failed" | "error";

export type VariantProvenance = {
  agent_version: string | null; model: string | null; prompt_version: string | null;
  workflow_version: string | null; git_commit_sha: string | null; deployment_id: string | null;
  metadata: Record<string, JsonValue>;
};
export type ExperimentVariant = { id: string; key: VariantKey; name: string; provenance: VariantProvenance };
export type ExperimentSubject = { position: number; a_trace_id: string; b_trace_id: string };
export type ExperimentCondition = {
  position: number; definition_id: string; definition_name: string; evaluator_kind: string;
  evaluator_config: Record<string, JsonValue>;
};
export type Experiment = {
  id: string; name: string; description: string | null; status: ExperimentStatus;
  created_at: string; updated_at: string; ready_at: string | null;
  variants: ExperimentVariant[]; subjects: ExperimentSubject[]; evaluation_conditions: ExperimentCondition[];
};
export type ExperimentSummary = Omit<Experiment, "variants" | "subjects" | "evaluation_conditions"> & {
  variant_count: number; subject_count: number; evaluation_condition_count: number;
};
export type ExperimentCreate = { name: string; description: string | null };
export type ExperimentConfigure = {
  name: string; description: string | null;
  variants: Array<{ key: VariantKey; name: string; provenance: VariantProvenance }>;
  subjects: Array<{ a_trace_id: string; b_trace_id: string }>;
  evaluation_definition_ids: string[];
};
export type ExperimentRun = {
  id: string; experiment_id: string; status: ExperimentRunStatus;
  expected_decision_count: number; completed_decision_count: number; evaluator_error_count: number;
  remaining_decision_count: number; error_category: string | null; error_message: string | null;
  created_at: string; queued_at: string | null; started_at: string | null; completed_at: string | null;
  attempt_count: number;
};
export type ExperimentResult = {
  run_id: string; experiment_id: string; subject_position: number; variant: VariantKey;
  condition_position: number; trace_id: string; definition_id: string; definition_name: string;
  evaluator_kind: string; evaluator_config: Record<string, JsonValue>; outcome: ExperimentOutcome;
  score: number | null; details: Record<string, JsonValue>; attempt_count: number; created_at: string;
};
export type EligibleAnalysis = {
  eligible: true; ineligible_reason: null; sample_size: number;
  a_passed_count: number; a_failed_count: number; b_passed_count: number; b_failed_count: number;
  a_pass_rate: number; b_pass_rate: number; pass_rate_difference: number;
  both_passed_count: number; both_passed_rate: number; both_failed_count: number; both_failed_rate: number;
  a_only_passed_count: number; a_only_passed_rate: number; b_only_passed_count: number; b_only_passed_rate: number;
  matched_pairs_odds_ratio: number | null; confidence_level: number; confidence_interval_lower: number;
  confidence_interval_upper: number; confidence_method: string; test_method: string; discordant_count: number;
  test_statistic: number | null; p_value: number; alpha: number; rejects_null: boolean;
};
export type IneligibleAnalysis = { eligible: false; ineligible_reason: "non_binary_outcome" };
export type ExperimentAnalysis = {
  id: string; run_id: string; experiment_id: string; analysis_schema_version: string;
  confidence_method: string; confidence_level: number; bootstrap_seed: number; bootstrap_iterations: number;
  hypothesis_test_method: string; alpha: number; created_at: string;
  conditions: Array<{ condition_position: number; definition_id: string; definition_name: string;
    evaluator_kind: string; result: EligibleAnalysis | IneligibleAnalysis }>;
};
export type ChangedSubject = {
  subject_position: number; condition_position: number; definition_id: string; definition_name: string;
  direction: ChangeDirection; a_trace_id: string; b_trace_id: string;
  a_outcome: "passed" | "failed"; b_outcome: "passed" | "failed";
};
export type Page<T> = { items: T[]; has_more: boolean };

export class ExperimentApiError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message); this.name = "ExperimentApiError"; this.status = status; this.code = code;
  }
}

export async function listExperiments(options: { status?: ExperimentStatus; offset?: number; pageSize?: number; signal?: AbortSignal } = {}): Promise<Page<ExperimentSummary>> {
  const params = pageParams(options.offset, options.pageSize);
  if (options.status) params.set("status", options.status);
  return parsePage(await request(`/api/v1/experiments?${params}`, { signal: options.signal }), parseExperimentSummary);
}
export async function createExperiment(payload: ExperimentCreate): Promise<Experiment> {
  return parseExperiment(await request("/api/v1/experiments", { method: "POST", body: payload }));
}
export async function getExperiment(id: string, signal?: AbortSignal): Promise<Experiment> {
  return parseExperiment(await request(`/api/v1/experiments/${encodeURIComponent(id)}`, { signal }));
}
export async function configureExperiment(id: string, payload: ExperimentConfigure): Promise<Experiment> {
  return parseExperiment(await request(`/api/v1/experiments/${encodeURIComponent(id)}/configuration`, { method: "POST", body: payload }));
}
export async function markExperimentReady(id: string): Promise<Experiment> {
  return parseExperiment(await request(`/api/v1/experiments/${encodeURIComponent(id)}/ready`, { method: "POST" }));
}
export async function listExperimentRuns(experimentId: string, signal?: AbortSignal): Promise<Page<ExperimentRun>> {
  return parsePage(await request(`/api/v1/experiments/${encodeURIComponent(experimentId)}/runs?page_size=100`, { signal }), parseExperimentRun);
}
export async function createExperimentRun(experimentId: string): Promise<ExperimentRun> {
  return parseExperimentRun(await request(`/api/v1/experiments/${encodeURIComponent(experimentId)}/runs`, { method: "POST" }));
}
export async function getExperimentRun(id: string, signal?: AbortSignal): Promise<ExperimentRun> {
  return parseExperimentRun(await request(`/api/v1/experiment-runs/${encodeURIComponent(id)}`, { signal }));
}
export async function executeExperimentRun(id: string): Promise<void> {
  await request(`/api/v1/experiment-runs/${encodeURIComponent(id)}/execute`, { method: "POST" });
}
export async function listExperimentResults(id: string, options: { variant?: VariantKey; condition?: number; offset?: number; signal?: AbortSignal } = {}): Promise<Page<ExperimentResult>> {
  const params = pageParams(options.offset, 20);
  if (options.variant) params.set("variant", options.variant);
  if (options.condition !== undefined) params.set("condition_position", String(options.condition));
  return parsePage(await request(`/api/v1/experiment-runs/${encodeURIComponent(id)}/results?${params}`, { signal: options.signal }), parseExperimentResult);
}
export async function getExperimentAnalysis(id: string, signal?: AbortSignal): Promise<ExperimentAnalysis | null> {
  try { return parseExperimentAnalysis(await request(`/api/v1/experiment-runs/${encodeURIComponent(id)}/analysis`, { signal })); }
  catch (error) { if (error instanceof ExperimentApiError && error.status === 404) return null; throw error; }
}
export async function createExperimentAnalysis(id: string): Promise<ExperimentAnalysis> {
  return parseExperimentAnalysis(await request(`/api/v1/experiment-runs/${encodeURIComponent(id)}/analysis`, { method: "POST" }));
}
export async function listChangedSubjects(id: string, condition: number, options: { direction?: ChangeDirection; offset?: number; signal?: AbortSignal } = {}): Promise<Page<ChangedSubject>> {
  const params = pageParams(options.offset, 20); params.set("condition_position", String(condition));
  if (options.direction) params.set("direction", options.direction);
  return parsePage(await request(`/api/v1/experiment-runs/${encodeURIComponent(id)}/analysis/changes?${params}`, { signal: options.signal }), parseChangedSubject);
}

export function parseExperimentRun(value: unknown): ExperimentRun {
  const item = record(value); const expected = integer(item.expected_decision_count); const completed = integer(item.completed_decision_count);
  const remaining = integer(item.remaining_decision_count);
  if (completed > expected || remaining !== expected - completed) invalid();
  return { id: string(item.id), experiment_id: string(item.experiment_id), status: enumValue(item.status, EXPERIMENT_RUN_STATUSES),
    expected_decision_count: expected, completed_decision_count: completed, evaluator_error_count: integer(item.evaluator_error_count),
    remaining_decision_count: remaining, error_category: nullableString(item.error_category), error_message: nullableString(item.error_message),
    created_at: string(item.created_at), queued_at: nullableString(item.queued_at), started_at: nullableString(item.started_at),
    completed_at: nullableString(item.completed_at), attempt_count: integer(item.attempt_count) };
}

export function parseExperimentAnalysis(value: unknown): ExperimentAnalysis {
  const item = record(value);
  return { id: string(item.id), run_id: string(item.run_id), experiment_id: string(item.experiment_id), analysis_schema_version: string(item.analysis_schema_version),
    confidence_method: string(item.confidence_method), confidence_level: finite(item.confidence_level), bootstrap_seed: integer(item.bootstrap_seed),
    bootstrap_iterations: integer(item.bootstrap_iterations), hypothesis_test_method: string(item.hypothesis_test_method), alpha: finite(item.alpha),
    created_at: string(item.created_at), conditions: array(item.conditions).map((raw) => { const condition = record(raw); const result = record(condition.result);
      return { condition_position: integer(condition.condition_position), definition_id: string(condition.definition_id), definition_name: string(condition.definition_name),
        evaluator_kind: string(condition.evaluator_kind), result: result.eligible === false
          ? { eligible: false as const, ineligible_reason: enumValue(result.ineligible_reason, ["non_binary_outcome"] as const) }
          : parseEligible(result) }; }) };
}

function parseEligible(item: Record<string, unknown>): EligibleAnalysis {
  if (item.eligible !== true || item.ineligible_reason !== null) invalid();
  return { eligible: true, ineligible_reason: null, sample_size: integer(item.sample_size),
    a_passed_count: integer(item.a_passed_count), a_failed_count: integer(item.a_failed_count), b_passed_count: integer(item.b_passed_count), b_failed_count: integer(item.b_failed_count),
    a_pass_rate: finite(item.a_pass_rate), b_pass_rate: finite(item.b_pass_rate), pass_rate_difference: finite(item.pass_rate_difference),
    both_passed_count: integer(item.both_passed_count), both_passed_rate: finite(item.both_passed_rate), both_failed_count: integer(item.both_failed_count), both_failed_rate: finite(item.both_failed_rate),
    a_only_passed_count: integer(item.a_only_passed_count), a_only_passed_rate: finite(item.a_only_passed_rate), b_only_passed_count: integer(item.b_only_passed_count), b_only_passed_rate: finite(item.b_only_passed_rate),
    matched_pairs_odds_ratio: nullableFinite(item.matched_pairs_odds_ratio), confidence_level: finite(item.confidence_level), confidence_interval_lower: finite(item.confidence_interval_lower),
    confidence_interval_upper: finite(item.confidence_interval_upper), confidence_method: string(item.confidence_method), test_method: string(item.test_method),
    discordant_count: integer(item.discordant_count), test_statistic: nullableFinite(item.test_statistic), p_value: finite(item.p_value), alpha: finite(item.alpha), rejects_null: boolean(item.rejects_null) };
}

function parseExperiment(value: unknown): Experiment { const item = record(value); return { ...baseExperiment(item),
  variants: array(item.variants).map((raw) => { const variant = record(raw); return { id: string(variant.id), key: enumValue(variant.key, ["A", "B"] as const), name: string(variant.name), provenance: parseProvenance(variant.provenance) }; }),
  subjects: array(item.subjects).map((raw) => { const subject = record(raw); return { position: integer(subject.position), a_trace_id: string(subject.a_trace_id), b_trace_id: string(subject.b_trace_id) }; }),
  evaluation_conditions: array(item.evaluation_conditions).map((raw) => { const condition = record(raw); return { position: integer(condition.position), definition_id: string(condition.definition_id), definition_name: string(condition.definition_name), evaluator_kind: string(condition.evaluator_kind), evaluator_config: jsonRecord(condition.evaluator_config) }; }) }; }
function parseExperimentSummary(value: unknown): ExperimentSummary { const item = record(value); return { ...baseExperiment(item), variant_count: integer(item.variant_count), subject_count: integer(item.subject_count), evaluation_condition_count: integer(item.evaluation_condition_count) }; }
function baseExperiment(item: Record<string, unknown>) { return { id: string(item.id), name: string(item.name), description: nullableString(item.description), status: enumValue(item.status, EXPERIMENT_STATUSES), created_at: string(item.created_at), updated_at: string(item.updated_at), ready_at: nullableString(item.ready_at) }; }
function parseProvenance(value: unknown): VariantProvenance { const item = record(value); return { agent_version: nullableString(item.agent_version), model: nullableString(item.model), prompt_version: nullableString(item.prompt_version), workflow_version: nullableString(item.workflow_version), git_commit_sha: nullableString(item.git_commit_sha), deployment_id: nullableString(item.deployment_id), metadata: jsonRecord(item.metadata) }; }
function parseExperimentResult(value: unknown): ExperimentResult { const item = record(value); return { run_id: string(item.run_id), experiment_id: string(item.experiment_id), subject_position: integer(item.subject_position), variant: enumValue(item.variant, ["A", "B"] as const), condition_position: integer(item.condition_position), trace_id: string(item.trace_id), definition_id: string(item.definition_id), definition_name: string(item.definition_name), evaluator_kind: string(item.evaluator_kind), evaluator_config: jsonRecord(item.evaluator_config), outcome: enumValue(item.outcome, ["passed", "failed", "error"] as const), score: nullableFinite(item.score), details: jsonRecord(item.details), attempt_count: integer(item.attempt_count), created_at: string(item.created_at) }; }
function parseChangedSubject(value: unknown): ChangedSubject { const item = record(value); return { subject_position: integer(item.subject_position), condition_position: integer(item.condition_position), definition_id: string(item.definition_id), definition_name: string(item.definition_name), direction: enumValue(item.direction, CHANGE_DIRECTIONS), a_trace_id: string(item.a_trace_id), b_trace_id: string(item.b_trace_id), a_outcome: enumValue(item.a_outcome, ["passed", "failed"] as const), b_outcome: enumValue(item.b_outcome, ["passed", "failed"] as const) }; }

async function request(path: string, options: { method?: "GET" | "POST"; body?: object; signal?: AbortSignal } = {}): Promise<unknown> {
  const response = await fetch(`${apiBaseUrl}${path}`, { method: options.method ?? "GET", signal: options.signal,
    headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) }, body: options.body ? JSON.stringify(options.body) : undefined });
  let data: unknown; try { data = await response.json(); } catch { throw new ExperimentApiError("The experiment service returned an unreadable response.", response.status); }
  if (!response.ok) { const outer = isRecord(data) ? data : null; const detail = outer && isRecord(outer.error) ? outer.error : outer && isRecord(outer.detail) ? outer.detail : null;
    throw new ExperimentApiError(detail && typeof detail.message === "string" ? detail.message : "Experiment request failed.", response.status, detail && typeof detail.code === "string" ? detail.code : undefined); }
  return data;
}
function pageParams(offset = 0, pageSize = 20) { return new URLSearchParams({ page_size: String(pageSize), offset: String(offset) }); }
function parsePage<T>(value: unknown, parse: (value: unknown) => T): Page<T> { const item = record(value); return { items: array(item.items).map(parse), has_more: boolean(item.has_more) }; }
function isRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function record(value: unknown): Record<string, unknown> { if (!isRecord(value)) invalid(); return value; }
function jsonRecord(value: unknown): Record<string, JsonValue> { return record(value) as Record<string, JsonValue>; }
function array(value: unknown): unknown[] { if (!Array.isArray(value)) invalid(); return value; }
function string(value: unknown): string { if (typeof value !== "string") invalid(); return value; }
function nullableString(value: unknown): string | null { if (value !== null && typeof value !== "string") invalid(); return value; }
function boolean(value: unknown): boolean { if (typeof value !== "boolean") invalid(); return value; }
function integer(value: unknown): number { if (!Number.isInteger(value) || (value as number) < 0) invalid(); return value as number; }
function finite(value: unknown): number { if (typeof value !== "number" || !Number.isFinite(value)) invalid(); return value; }
function nullableFinite(value: unknown): number | null { return value === null ? null : finite(value); }
function enumValue<T extends string>(value: unknown, allowed: readonly T[]): T { if (typeof value !== "string" || !allowed.includes(value as T)) invalid(); return value as T; }
function invalid(): never { throw new ExperimentApiError("The experiment service returned an unexpected response.", 502, "INVALID_RESPONSE"); }
