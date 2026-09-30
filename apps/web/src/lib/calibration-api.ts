import { apiBaseUrl } from "./api-base-url.ts";

export const REFERENCE_SET_STATUSES = ["draft", "labeling", "frozen"] as const;
export const HUMAN_LABELS = ["passed", "failed"] as const;
export const JUDGE_RUN_STATUSES = ["pending", "queued", "running", "completed", "failed"] as const;
export const DISAGREEMENT_CATEGORIES = ["false_positive", "false_negative"] as const;

export type ReferenceSetStatus = (typeof REFERENCE_SET_STATUSES)[number];
export type HumanLabel = (typeof HUMAN_LABELS)[number];
export type JudgeRunStatus = (typeof JUDGE_RUN_STATUSES)[number];
export type DisagreementCategory = (typeof DISAGREEMENT_CATEGORIES)[number];

export type ReferenceSet = {
  id: string; name: string; description: string | null; status: ReferenceSetStatus;
  created_at: string; updated_at: string; frozen_at: string | null;
  subject_count: number; annotation_count: number; reference_count: number;
  unlabeled_count: number; passed_count: number; failed_count: number;
};
export type ReferenceSubject = {
  reference_set_id: string; trace_id: string; position: number; trace_name: string;
  reference_label: HumanLabel | null; reference_rationale: string | null;
  reference_annotator_id: string | null; reference_labeled_at: string | null;
  annotation_count: number; created_at: string;
};
export type CalibrationStudy = {
  id: string; name: string; description: string | null; reference_set_id: string;
  status: "draft"; subject_count: number; created_at: string;
};
export type JudgeConfiguration = {
  id: string; name: string; description: string | null; provider: "openai"; model: string;
  rubric: string; output_schema_version: "1"; timeout_seconds: number;
  max_output_tokens: number; configuration_version: "1"; created_at: string;
};
export type JudgeRun = {
  id: string; study_id: string; configuration_id: string; provider: string; model: string;
  rubric: string; output_schema_version: string; timeout_seconds: number;
  max_output_tokens: number; configuration_version: string; status: JudgeRunStatus;
  error_category: string | null; subject_count: number; result_count: number;
  created_at: string; queued_at: string | null; started_at: string | null;
  completed_at: string | null; attempt_count: number;
};
export type JudgeProgress = {
  run_id: string; status: JudgeRunStatus; subject_count: number; result_count: number;
  passed_count: number; failed_count: number; error_count: number; pending_count: number;
};
export type JudgeResult = {
  run_id: string; study_id: string; trace_id: string; decision: HumanLabel | null;
  rationale: string | null; error_category: string | null; provider_request_id: string | null;
  provider: string; model: string; input_tokens: number | null; output_tokens: number | null;
  total_tokens: number | null; latency_ms: number | null; attempt_count: number; created_at: string;
};
export type CalibrationAnalysis = {
  run_id: string; study_id: string; metric_schema_version: string; created_at: string;
  sample_count: number; human_passed_count: number; human_failed_count: number;
  judge_passed_count: number; judge_failed_count: number; agreement_count: number;
  disagreement_count: number; true_positive: number; true_negative: number;
  false_positive: number; false_negative: number; observed_agreement: number;
  expected_agreement: number; precision_passed: number | null; recall_passed: number | null;
  f1_passed: number | null; specificity_failed: number | null; cohens_kappa: number | null;
  kappa_is_defined: boolean; undefined_metrics: string[];
};
export type CalibrationDisagreement = {
  trace_id: string; category: DisagreementCategory; human_label: HumanLabel;
  judge_decision: HumanLabel;
};
export type Page<T> = { items: T[]; has_more: boolean };

export type ReferenceSetCreate = { name: string; description: string | null };
export type StudyCreate = { name: string; description: string | null; reference_set_id: string };
export type ConfigurationCreate = Omit<JudgeConfiguration, "id" | "created_at">;

export class CalibrationApiError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "CalibrationApiError";
    this.status = status;
    this.code = code;
  }
}

export async function listReferenceSets(signal?: AbortSignal): Promise<Page<ReferenceSet>> {
  return parsePage(await request("/api/v1/human-reference-sets?page_size=100", { signal }), parseReferenceSet);
}
export async function getReferenceSet(id: string, signal?: AbortSignal): Promise<ReferenceSet> {
  return parseReferenceSet(await request(`/api/v1/human-reference-sets/${encodeURIComponent(id)}`, { signal }));
}
export async function createReferenceSet(payload: ReferenceSetCreate, signal?: AbortSignal): Promise<ReferenceSet> {
  return parseReferenceSet(await request("/api/v1/human-reference-sets", { method: "POST", body: payload, signal }));
}
export async function defineReferenceSubjects(id: string, traceIds: string[]): Promise<ReferenceSet> {
  return parseReferenceSet(await request(`/api/v1/human-reference-sets/${encodeURIComponent(id)}/subjects`, {
    method: "POST", body: { trace_ids: traceIds },
  }));
}
export async function listReferenceSubjects(id: string, offset = 0, signal?: AbortSignal): Promise<Page<ReferenceSubject>> {
  return parsePage(await request(`/api/v1/human-reference-sets/${encodeURIComponent(id)}/subjects?page_size=100&offset=${offset}`, { signal }), parseReferenceSubject);
}
export async function beginReferenceLabeling(id: string): Promise<ReferenceSet> {
  return parseReferenceSet(await request(`/api/v1/human-reference-sets/${encodeURIComponent(id)}/begin-labeling`, { method: "POST" }));
}
export async function writeHumanAnnotation(
  id: string,
  payload: { trace_id: string; annotator_id: string; label: HumanLabel; rationale: string | null; source: "manual" },
): Promise<void> {
  await request(`/api/v1/human-reference-sets/${encodeURIComponent(id)}/annotations`, { method: "POST", body: payload });
}
export async function setReferenceLabel(
  id: string,
  traceId: string,
  payload: { label: HumanLabel; annotator_id: string; rationale: string | null },
): Promise<void> {
  await request(`/api/v1/human-reference-sets/${encodeURIComponent(id)}/subjects/${encodeURIComponent(traceId)}/reference-label`, { method: "POST", body: payload });
}
export async function freezeReferenceSet(id: string): Promise<ReferenceSet> {
  return parseReferenceSet(await request(`/api/v1/human-reference-sets/${encodeURIComponent(id)}/freeze`, { method: "POST" }));
}

export async function listStudies(signal?: AbortSignal): Promise<Page<CalibrationStudy>> {
  return parsePage(await request("/api/v1/calibration-studies?page_size=100", { signal }), parseStudy);
}
export async function getStudy(id: string, signal?: AbortSignal): Promise<CalibrationStudy> {
  return parseStudy(await request(`/api/v1/calibration-studies/${encodeURIComponent(id)}`, { signal }));
}
export async function createStudy(payload: StudyCreate): Promise<CalibrationStudy> {
  return parseStudy(await request("/api/v1/calibration-studies", { method: "POST", body: payload }));
}

export async function listJudgeConfigurations(signal?: AbortSignal): Promise<Page<JudgeConfiguration>> {
  return parsePage(await request("/api/v1/judge-configurations?page_size=100", { signal }), parseConfiguration);
}
export async function getJudgeConfiguration(id: string, signal?: AbortSignal): Promise<JudgeConfiguration> {
  return parseConfiguration(await request(`/api/v1/judge-configurations/${encodeURIComponent(id)}`, { signal }));
}
export async function createJudgeConfiguration(payload: ConfigurationCreate): Promise<JudgeConfiguration> {
  return parseConfiguration(await request("/api/v1/judge-configurations", { method: "POST", body: payload }));
}

export async function listJudgeRuns(signal?: AbortSignal): Promise<Page<JudgeRun>> {
  return parsePage(await request("/api/v1/calibration-judge-runs?page_size=100", { signal }), parseJudgeRun);
}
export async function getJudgeRun(id: string, signal?: AbortSignal): Promise<JudgeRun> {
  return parseJudgeRun(await request(`/api/v1/calibration-judge-runs/${encodeURIComponent(id)}`, { signal }));
}
export async function createJudgeRun(studyId: string, configurationId: string): Promise<JudgeRun> {
  return parseJudgeRun(await request("/api/v1/calibration-judge-runs", {
    method: "POST", body: { study_id: studyId, configuration_id: configurationId },
  }));
}
export async function executeJudgeRun(id: string): Promise<{ run_id: string; status: JudgeRunStatus }> {
  const item = expectRecord(await request(`/api/v1/calibration-judge-runs/${encodeURIComponent(id)}/execute`, { method: "POST" }));
  return { run_id: expectString(item.run_id), status: expectEnum(item.status, JUDGE_RUN_STATUSES) };
}
export async function getJudgeProgress(id: string, signal?: AbortSignal): Promise<JudgeProgress> {
  return parseJudgeProgress(await request(`/api/v1/calibration-judge-runs/${encodeURIComponent(id)}/progress`, { signal }));
}
export async function listJudgeResults(id: string, offset = 0, signal?: AbortSignal): Promise<Page<JudgeResult>> {
  return parsePage(await request(`/api/v1/calibration-judge-runs/${encodeURIComponent(id)}/results?page_size=100&offset=${offset}`, { signal }), parseJudgeResult);
}
export async function listAllJudgeResults(id: string, signal?: AbortSignal): Promise<JudgeResult[]> {
  const items: JudgeResult[] = [];
  for (let offset = 0; offset <= 400; offset += 100) {
    const page = await listJudgeResults(id, offset, signal);
    items.push(...page.items);
    if (!page.has_more) return items;
  }
  return items;
}
export async function getCalibrationAnalysis(id: string, signal?: AbortSignal): Promise<CalibrationAnalysis> {
  return parseCalibrationAnalysis(await request(`/api/v1/calibration-judge-runs/${encodeURIComponent(id)}/analysis`, { signal }));
}
export async function createCalibrationAnalysis(id: string): Promise<CalibrationAnalysis> {
  return parseCalibrationAnalysis(await request(`/api/v1/calibration-judge-runs/${encodeURIComponent(id)}/analysis`, { method: "POST" }));
}
export async function listDisagreements(
  id: string,
  options: { category?: DisagreementCategory; offset?: number; pageSize?: number; signal?: AbortSignal } = {},
): Promise<Page<CalibrationDisagreement>> {
  const params = new URLSearchParams({ page_size: String(options.pageSize ?? 20), offset: String(options.offset ?? 0) });
  if (options.category) params.set("category", options.category);
  return parsePage(await request(`/api/v1/calibration-judge-runs/${encodeURIComponent(id)}/disagreements?${params}`, { signal: options.signal }), parseDisagreement);
}

function parseReferenceSet(value: unknown): ReferenceSet {
  const item = expectRecord(value);
  const subjectCount = expectInteger(item.subject_count, 0, 500);
  const referenceCount = expectInteger(item.reference_count, 0, subjectCount);
  const unlabeledCount = expectInteger(item.unlabeled_count, 0, subjectCount);
  const passedCount = expectInteger(item.passed_count, 0, subjectCount);
  const failedCount = expectInteger(item.failed_count, 0, subjectCount);
  if (referenceCount + unlabeledCount !== subjectCount || passedCount + failedCount !== referenceCount) invalidResponse();
  return {
    id: expectString(item.id), name: expectString(item.name), description: expectNullableString(item.description),
    status: expectEnum(item.status, REFERENCE_SET_STATUSES), created_at: expectString(item.created_at),
    updated_at: expectString(item.updated_at), frozen_at: expectNullableString(item.frozen_at), subject_count: subjectCount,
    annotation_count: expectInteger(item.annotation_count, 0, 500_000), reference_count: referenceCount,
    unlabeled_count: unlabeledCount, passed_count: passedCount, failed_count: failedCount,
  };
}
function parseReferenceSubject(value: unknown): ReferenceSubject {
  const item = expectRecord(value);
  return {
    reference_set_id: expectString(item.reference_set_id), trace_id: expectString(item.trace_id),
    position: expectInteger(item.position, 0, 499), trace_name: expectString(item.trace_name),
    reference_label: expectNullableEnum(item.reference_label, HUMAN_LABELS),
    reference_rationale: expectNullableString(item.reference_rationale),
    reference_annotator_id: expectNullableString(item.reference_annotator_id),
    reference_labeled_at: expectNullableString(item.reference_labeled_at),
    annotation_count: expectInteger(item.annotation_count, 0, 500_000), created_at: expectString(item.created_at),
  };
}
function parseStudy(value: unknown): CalibrationStudy {
  const item = expectRecord(value);
  return {
    id: expectString(item.id), name: expectString(item.name), description: expectNullableString(item.description),
    reference_set_id: expectString(item.reference_set_id), status: expectEnum(item.status, ["draft"] as const),
    subject_count: expectInteger(item.subject_count, 1, 500), created_at: expectString(item.created_at),
  };
}
function parseConfiguration(value: unknown): JudgeConfiguration {
  const item = expectRecord(value);
  return {
    id: expectString(item.id), name: expectString(item.name), description: expectNullableString(item.description),
    provider: expectEnum(item.provider, ["openai"] as const), model: expectString(item.model), rubric: expectString(item.rubric),
    output_schema_version: expectEnum(item.output_schema_version, ["1"] as const),
    timeout_seconds: expectInteger(item.timeout_seconds, 5, 300), max_output_tokens: expectInteger(item.max_output_tokens, 32, 1_000),
    configuration_version: expectEnum(item.configuration_version, ["1"] as const), created_at: expectString(item.created_at),
  };
}
function parseJudgeRun(value: unknown): JudgeRun {
  const item = expectRecord(value);
  const subjectCount = expectInteger(item.subject_count, 0, 500);
  const resultCount = expectInteger(item.result_count, 0, subjectCount);
  return {
    id: expectString(item.id), study_id: expectString(item.study_id), configuration_id: expectString(item.configuration_id),
    provider: expectString(item.provider), model: expectString(item.model), rubric: expectString(item.rubric),
    output_schema_version: expectString(item.output_schema_version), timeout_seconds: expectInteger(item.timeout_seconds, 5, 300),
    max_output_tokens: expectInteger(item.max_output_tokens, 32, 1_000), configuration_version: expectString(item.configuration_version),
    status: expectEnum(item.status, JUDGE_RUN_STATUSES), error_category: expectNullableString(item.error_category),
    subject_count: subjectCount, result_count: resultCount, created_at: expectString(item.created_at),
    queued_at: expectNullableString(item.queued_at), started_at: expectNullableString(item.started_at),
    completed_at: expectNullableString(item.completed_at), attempt_count: expectInteger(item.attempt_count, 0, 3),
  };
}
export function parseJudgeProgress(value: unknown): JudgeProgress {
  const item = expectRecord(value);
  const subjectCount = expectInteger(item.subject_count, 0, 500);
  const resultCount = expectInteger(item.result_count, 0, subjectCount);
  const passedCount = expectInteger(item.passed_count, 0, resultCount);
  const failedCount = expectInteger(item.failed_count, 0, resultCount);
  const errorCount = expectInteger(item.error_count, 0, resultCount);
  const pendingCount = expectInteger(item.pending_count, 0, subjectCount);
  if (passedCount + failedCount + errorCount !== resultCount || resultCount + pendingCount !== subjectCount) invalidResponse();
  return {
    run_id: expectString(item.run_id), status: expectEnum(item.status, JUDGE_RUN_STATUSES), subject_count: subjectCount,
    result_count: resultCount, passed_count: passedCount, failed_count: failedCount, error_count: errorCount, pending_count: pendingCount,
  };
}
function parseJudgeResult(value: unknown): JudgeResult {
  const item = expectRecord(value);
  const decision = expectNullableEnum(item.decision, HUMAN_LABELS);
  const errorCategory = expectNullableString(item.error_category);
  if ((decision === null) === (errorCategory === null)) invalidResponse();
  return {
    run_id: expectString(item.run_id), study_id: expectString(item.study_id), trace_id: expectString(item.trace_id),
    decision, rationale: expectNullableString(item.rationale), error_category: errorCategory,
    provider_request_id: expectNullableString(item.provider_request_id), provider: expectString(item.provider), model: expectString(item.model),
    input_tokens: expectNullableInteger(item.input_tokens, 0), output_tokens: expectNullableInteger(item.output_tokens, 0),
    total_tokens: expectNullableInteger(item.total_tokens, 0), latency_ms: expectNullableFinite(item.latency_ms, 0, Number.MAX_SAFE_INTEGER),
    attempt_count: expectInteger(item.attempt_count, 1, 3), created_at: expectString(item.created_at),
  };
}
export function parseCalibrationAnalysis(value: unknown): CalibrationAnalysis {
  const item = expectRecord(value);
  const sampleCount = expectInteger(item.sample_count, 1, 500);
  const analysis = {
    run_id: expectString(item.run_id), study_id: expectString(item.study_id), metric_schema_version: expectString(item.metric_schema_version),
    created_at: expectString(item.created_at), sample_count: sampleCount,
    human_passed_count: expectInteger(item.human_passed_count, 0, sampleCount), human_failed_count: expectInteger(item.human_failed_count, 0, sampleCount),
    judge_passed_count: expectInteger(item.judge_passed_count, 0, sampleCount), judge_failed_count: expectInteger(item.judge_failed_count, 0, sampleCount),
    agreement_count: expectInteger(item.agreement_count, 0, sampleCount), disagreement_count: expectInteger(item.disagreement_count, 0, sampleCount),
    true_positive: expectInteger(item.true_positive, 0, sampleCount), true_negative: expectInteger(item.true_negative, 0, sampleCount),
    false_positive: expectInteger(item.false_positive, 0, sampleCount), false_negative: expectInteger(item.false_negative, 0, sampleCount),
    observed_agreement: expectFinite(item.observed_agreement, 0, 1), expected_agreement: expectFinite(item.expected_agreement, 0, 1),
    precision_passed: expectNullableFinite(item.precision_passed, 0, 1), recall_passed: expectNullableFinite(item.recall_passed, 0, 1),
    f1_passed: expectNullableFinite(item.f1_passed, 0, 1), specificity_failed: expectNullableFinite(item.specificity_failed, 0, 1),
    cohens_kappa: expectNullableFinite(item.cohens_kappa, -1, 1), kappa_is_defined: expectBoolean(item.kappa_is_defined),
    undefined_metrics: expectStringArray(item.undefined_metrics),
  };
  if (analysis.human_passed_count + analysis.human_failed_count !== sampleCount
      || analysis.judge_passed_count + analysis.judge_failed_count !== sampleCount
      || analysis.agreement_count + analysis.disagreement_count !== sampleCount
      || analysis.true_positive + analysis.true_negative + analysis.false_positive + analysis.false_negative !== sampleCount) invalidResponse();
  return analysis;
}
function parseDisagreement(value: unknown): CalibrationDisagreement {
  const item = expectRecord(value);
  return {
    trace_id: expectString(item.trace_id), category: expectEnum(item.category, DISAGREEMENT_CATEGORIES),
    human_label: expectEnum(item.human_label, HUMAN_LABELS), judge_decision: expectEnum(item.judge_decision, HUMAN_LABELS),
  };
}

async function request(
  path: string,
  options: { method?: "GET" | "POST"; body?: object; signal?: AbortSignal } = {},
): Promise<unknown> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: options.method ?? "GET", signal: options.signal,
    headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (response.status === 204) return null;
  let data: unknown;
  try { data = await response.json(); } catch {
    throw new CalibrationApiError("The calibration service returned an unreadable response.", response.status);
  }
  if (!response.ok) {
    const record = isRecord(data) && isRecord(data.error) ? data.error : null;
    throw new CalibrationApiError(
      record && typeof record.message === "string" ? record.message : "Calibration request failed.",
      response.status,
      record && typeof record.code === "string" ? record.code : undefined,
    );
  }
  return data;
}

function parsePage<T>(value: unknown, parser: (item: unknown) => T): Page<T> {
  const page = expectRecord(value);
  return { items: expectArray(page.items).map(parser), has_more: expectBoolean(page.has_more) };
}
function isRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function expectRecord(value: unknown): Record<string, unknown> { if (!isRecord(value)) invalidResponse(); return value; }
function expectArray(value: unknown): unknown[] { if (!Array.isArray(value)) invalidResponse(); return value; }
function expectString(value: unknown): string { if (typeof value !== "string") invalidResponse(); return value; }
function expectNullableString(value: unknown): string | null { if (value !== null && typeof value !== "string") invalidResponse(); return value; }
function expectBoolean(value: unknown): boolean { if (typeof value !== "boolean") invalidResponse(); return value; }
function expectStringArray(value: unknown): string[] { const items = expectArray(value); if (!items.every((item) => typeof item === "string")) invalidResponse(); return items as string[]; }
function expectInteger(value: unknown, minimum: number, maximum: number): number { if (!Number.isInteger(value) || (value as number) < minimum || (value as number) > maximum) invalidResponse(); return value as number; }
function expectNullableInteger(value: unknown, minimum: number): number | null { return value === null ? null : expectInteger(value, minimum, Number.MAX_SAFE_INTEGER); }
function expectFinite(value: unknown, minimum: number, maximum: number): number { if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum) invalidResponse(); return value; }
function expectNullableFinite(value: unknown, minimum: number, maximum: number): number | null { return value === null ? null : expectFinite(value, minimum, maximum); }
function expectEnum<T extends string>(value: unknown, allowed: readonly T[]): T { if (typeof value !== "string" || !allowed.includes(value as T)) invalidResponse(); return value as T; }
function expectNullableEnum<T extends string>(value: unknown, allowed: readonly T[]): T | null { return value === null ? null : expectEnum(value, allowed); }
function invalidResponse(): never { throw new CalibrationApiError("The calibration service returned an unexpected response.", 502, "INVALID_RESPONSE"); }
