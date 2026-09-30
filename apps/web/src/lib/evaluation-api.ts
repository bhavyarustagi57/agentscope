import type { JsonValue } from "./trace-api";
import { apiBaseUrl } from "./api-base-url.ts";

export const EVALUATOR_KINDS = ["exact_match", "contains", "numeric_threshold"] as const;
export const RUN_STATUSES = ["pending", "queued", "running", "completed", "failed"] as const;
export const EVALUATION_OUTCOMES = ["passed", "failed", "error"] as const;

export type EvaluatorKind = (typeof EVALUATOR_KINDS)[number];
export type EvaluationRunStatus = (typeof RUN_STATUSES)[number];
export type EvaluationOutcome = (typeof EVALUATION_OUTCOMES)[number];

export type EvaluationDefinitionCreate = {
  name: string;
  description: string | null;
  evaluator_kind: EvaluatorKind;
  evaluator_config: Record<string, JsonValue>;
  is_enabled: boolean;
};

export type EvaluationDefinition = EvaluationDefinitionCreate & {
  id: string;
  created_at: string;
  updated_at: string;
};

export type EvaluationRun = {
  id: string;
  definition_id: string;
  definition_name: string;
  evaluator_kind: EvaluatorKind;
  evaluator_config: Record<string, JsonValue>;
  status: EvaluationRunStatus;
  error_message: string | null;
  created_at: string;
  queued_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  subject_count: number;
  result_count: number;
  passed_count: number;
  failed_count: number;
  error_count: number;
  scored_count: number;
  average_score: number | null;
};

export type EvaluationResult = {
  id: string;
  run_id: string;
  trace_id: string;
  trace_name: string | null;
  outcome: EvaluationOutcome;
  score: number | null;
  details: Record<string, JsonValue>;
  created_at: string;
};

export type EvaluationExecutionAccepted = {
  run_id: string;
  status: "queued";
  subject_count: number;
  queue_delivery: "enqueued" | "deferred";
};

export type EvaluationDefinitionList = { items: EvaluationDefinition[]; has_more: boolean };
export type EvaluationRunList = { items: EvaluationRun[]; has_more: boolean };
export type EvaluationResultList = { items: EvaluationResult[]; has_more: boolean };

export class EvaluationApiError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "EvaluationApiError";
    this.status = status;
    this.code = code;
  }
}

export async function listEvaluationDefinitions(signal?: AbortSignal): Promise<EvaluationDefinitionList> {
  const data = expectRecord(await request("/api/v1/evaluation-definitions?page_size=100", { signal }));
  return { items: expectArray(data.items).map(parseEvaluationDefinition), has_more: expectBoolean(data.has_more) };
}

export async function createEvaluationDefinition(
  payload: EvaluationDefinitionCreate,
  signal?: AbortSignal,
): Promise<EvaluationDefinition> {
  return parseEvaluationDefinition(await request("/api/v1/evaluation-definitions", { method: "POST", body: payload, signal }));
}

export async function listEvaluationRuns(signal?: AbortSignal): Promise<EvaluationRunList> {
  const data = expectRecord(await request("/api/v1/evaluation-runs?page_size=100", { signal }));
  return { items: expectArray(data.items).map(parseEvaluationRun), has_more: expectBoolean(data.has_more) };
}

export async function getEvaluationRun(runId: string, signal?: AbortSignal): Promise<EvaluationRun> {
  return parseEvaluationRun(await request(`/api/v1/evaluation-runs/${encodeURIComponent(runId)}`, { signal }));
}

export async function createEvaluationRun(
  definitionId: string,
  signal?: AbortSignal,
): Promise<EvaluationRun> {
  return parseEvaluationRun(await request("/api/v1/evaluation-runs", {
    method: "POST",
    body: { definition_id: definitionId },
    signal,
  }));
}

export async function executeEvaluationRun(
  runId: string,
  traceIds: string[],
  signal?: AbortSignal,
): Promise<EvaluationExecutionAccepted> {
  const data = expectRecord(await request(`/api/v1/evaluation-runs/${encodeURIComponent(runId)}/execute`, {
    method: "POST",
    body: { trace_ids: traceIds },
    signal,
  }));
  const status = expectEnum(data.status, ["queued"] as const);
  const queueDelivery = expectEnum(data.queue_delivery, ["enqueued", "deferred"] as const);
  return {
    run_id: expectString(data.run_id),
    status,
    subject_count: expectBoundedInteger(data.subject_count, 1, 1_000),
    queue_delivery: queueDelivery,
  };
}

export async function listEvaluationResults(
  runId: string,
  options: { outcome?: EvaluationOutcome; offset?: number; pageSize?: number; signal?: AbortSignal } = {},
): Promise<EvaluationResultList> {
  const params = new URLSearchParams({
    page_size: String(options.pageSize ?? 20),
    offset: String(options.offset ?? 0),
  });
  if (options.outcome) params.set("outcome", options.outcome);
  const data = expectRecord(await request(
    `/api/v1/evaluation-runs/${encodeURIComponent(runId)}/results?${params}`,
    { signal: options.signal },
  ));
  return { items: expectArray(data.items).map(parseEvaluationResult), has_more: expectBoolean(data.has_more) };
}

export function parseEvaluationRun(value: unknown): EvaluationRun {
  const item = expectRecord(value);
  const subjectCount = expectBoundedInteger(item.subject_count, 0, 1_000);
  const resultCount = expectBoundedInteger(item.result_count, 0, 1_000);
  const passedCount = expectBoundedInteger(item.passed_count, 0, 1_000);
  const failedCount = expectBoundedInteger(item.failed_count, 0, 1_000);
  const errorCount = expectBoundedInteger(item.error_count, 0, 1_000);
  const scoredCount = expectBoundedInteger(item.scored_count, 0, 1_000);
  if (resultCount > subjectCount || passedCount + failedCount + errorCount !== resultCount || scoredCount > resultCount) {
    invalidResponse();
  }
  return {
    id: expectString(item.id),
    definition_id: expectString(item.definition_id),
    definition_name: expectString(item.definition_name),
    evaluator_kind: expectEnum(item.evaluator_kind, EVALUATOR_KINDS),
    evaluator_config: expectJsonRecord(item.evaluator_config),
    status: expectEnum(item.status, RUN_STATUSES),
    error_message: expectNullableString(item.error_message),
    created_at: expectString(item.created_at),
    queued_at: expectNullableString(item.queued_at),
    started_at: expectNullableString(item.started_at),
    completed_at: expectNullableString(item.completed_at),
    subject_count: subjectCount,
    result_count: resultCount,
    passed_count: passedCount,
    failed_count: failedCount,
    error_count: errorCount,
    scored_count: scoredCount,
    average_score: expectNullableScore(item.average_score),
  };
}

function parseEvaluationDefinition(value: unknown): EvaluationDefinition {
  const item = expectRecord(value);
  return {
    id: expectString(item.id),
    name: expectString(item.name),
    description: expectNullableString(item.description),
    evaluator_kind: expectEnum(item.evaluator_kind, EVALUATOR_KINDS),
    evaluator_config: expectJsonRecord(item.evaluator_config),
    is_enabled: expectBoolean(item.is_enabled),
    created_at: expectString(item.created_at),
    updated_at: expectString(item.updated_at),
  };
}

function parseEvaluationResult(value: unknown): EvaluationResult {
  const item = expectRecord(value);
  return {
    id: expectString(item.id),
    run_id: expectString(item.run_id),
    trace_id: expectString(item.trace_id),
    trace_name: expectNullableString(item.trace_name),
    outcome: expectEnum(item.outcome, EVALUATION_OUTCOMES),
    score: expectNullableScore(item.score),
    details: expectJsonRecord(item.details),
    created_at: expectString(item.created_at),
  };
}

async function request(
  path: string,
  options: { method?: "GET" | "POST"; body?: object; signal?: AbortSignal } = {},
): Promise<unknown> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: options.method ?? "GET",
    signal: options.signal,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new EvaluationApiError("The evaluation service returned an unreadable response.", response.status);
  }
  if (!response.ok) {
    const record = isRecord(data) && isRecord(data.error) ? data.error : null;
    throw new EvaluationApiError(
      record && typeof record.message === "string" ? record.message : "Evaluation request failed.",
      response.status,
      record && typeof record.code === "string" ? record.code : undefined,
    );
  }
  return data;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function expectRecord(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) invalidResponse();
  return value;
}
function expectJsonRecord(value: unknown): Record<string, JsonValue> {
  return expectRecord(value) as Record<string, JsonValue>;
}
function expectArray(value: unknown): unknown[] {
  if (!Array.isArray(value)) invalidResponse();
  return value;
}
function expectString(value: unknown): string {
  if (typeof value !== "string") invalidResponse();
  return value;
}
function expectNullableString(value: unknown): string | null {
  if (value !== null && typeof value !== "string") invalidResponse();
  return value;
}
function expectBoolean(value: unknown): boolean {
  if (typeof value !== "boolean") invalidResponse();
  return value;
}
function expectBoundedInteger(value: unknown, minimum: number, maximum: number): number {
  if (!Number.isInteger(value) || (value as number) < minimum || (value as number) > maximum) invalidResponse();
  return value as number;
}
function expectNullableScore(value: unknown): number | null {
  if (value !== null && (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1)) invalidResponse();
  return value;
}
function expectEnum<T extends string>(value: unknown, allowed: readonly T[]): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) invalidResponse();
  return value as T;
}
function invalidResponse(): never {
  throw new EvaluationApiError("The evaluation service returned an unexpected response.", 502, "INVALID_RESPONSE");
}
