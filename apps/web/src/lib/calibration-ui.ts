import type {
  CalibrationAnalysis,
  ConfigurationCreate,
  DisagreementCategory,
  JudgeRunStatus,
  JudgeResult,
  ReferenceSetCreate,
  ReferenceSetStatus,
  StudyCreate,
} from "./calibration-api";

export const calibrationSections = [
  { id: "reference-sets", label: "Reference Sets" },
  { id: "studies", label: "Studies" },
  { id: "judge-configurations", label: "Judge Configurations" },
  { id: "judge-runs", label: "Judge Runs" },
] as const;

export function buildReferenceSetPayload(draft: { name: string; description: string }): {
  payload: ReferenceSetCreate | null; errors: Record<string, string>;
} {
  const name = draft.name.trim();
  const description = draft.description.trim();
  const errors: Record<string, string> = {};
  if (!name) errors.name = "Name is required.";
  else if (name.length > 200) errors.name = "Name must be 200 characters or fewer.";
  if (description.length > 2_000) errors.description = "Description must be 2,000 characters or fewer.";
  return { payload: Object.keys(errors).length ? null : { name, description: description || null }, errors };
}

export function buildStudyPayload(draft: { name: string; description: string; referenceSetId: string }): {
  payload: StudyCreate | null; errors: Record<string, string>;
} {
  const name = draft.name.trim();
  const description = draft.description.trim();
  const errors: Record<string, string> = {};
  if (!name) errors.name = "Name is required.";
  else if (name.length > 200) errors.name = "Name must be 200 characters or fewer.";
  if (description.length > 2_000) errors.description = "Description must be 2,000 characters or fewer.";
  if (!draft.referenceSetId) errors.referenceSetId = "Choose a frozen reference set.";
  return { payload: Object.keys(errors).length ? null : { name, description: description || null, reference_set_id: draft.referenceSetId }, errors };
}

export function buildConfigurationPayload(draft: {
  name: string; description: string; model: string; rubric: string;
  timeoutSeconds: string; maxOutputTokens: string;
}): { payload: ConfigurationCreate | null; errors: Record<string, string> } {
  const name = draft.name.trim();
  const description = draft.description.trim();
  const model = draft.model.trim();
  const rubric = draft.rubric.trim();
  const timeout = Number(draft.timeoutSeconds);
  const outputTokens = Number(draft.maxOutputTokens);
  const errors: Record<string, string> = {};
  if (!name) errors.name = "Name is required.";
  else if (name.length > 200) errors.name = "Name must be 200 characters or fewer.";
  if (description.length > 2_000) errors.description = "Description must be 2,000 characters or fewer.";
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(model)) errors.model = "Enter a valid model identifier (letters, numbers, dot, underscore, colon, or hyphen).";
  if (!rubric) errors.rubric = "Rubric/instructions are required.";
  else if (rubric.length > 8_000) errors.rubric = "Rubric must be 8,000 characters or fewer.";
  if (!Number.isInteger(timeout) || timeout < 5 || timeout > 300) errors.timeoutSeconds = "Timeout must be an integer from 5 to 300 seconds.";
  if (!Number.isInteger(outputTokens) || outputTokens < 32 || outputTokens > 1_000) errors.maxOutputTokens = "Output-token limit must be an integer from 32 to 1,000.";
  return {
    payload: Object.keys(errors).length ? null : {
      name, description: description || null, provider: "openai", model, rubric,
      output_schema_version: "1", timeout_seconds: timeout, max_output_tokens: outputTokens,
      configuration_version: "1",
    },
    errors,
  };
}

export function parseTraceIds(value: string): { traceIds: string[]; error: string | null } {
  const items = value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);
  if (!items.length) return { traceIds: [], error: "Enter at least one trace ID." };
  if (items.length > 500) return { traceIds: [], error: "A reference set can contain at most 500 traces." };
  if (new Set(items).size !== items.length) return { traceIds: [], error: "Trace IDs must be unique." };
  if (items.some((item) => item.length > 128)) return { traceIds: [], error: "Trace IDs must be 128 characters or fewer." };
  return { traceIds: items, error: null };
}

export function referenceSetActions(status: ReferenceSetStatus, subjects: number, references: number) {
  return {
    canDefineSubjects: status === "draft",
    canBeginLabeling: status === "draft" && subjects > 0,
    canLabel: status === "labeling",
    canFreeze: status === "labeling" && subjects > 0 && references === subjects,
  };
}

export function shouldPollJudgeRun(status: JudgeRunStatus): boolean {
  return status === "queued" || status === "running";
}

export function isProviderFailure(result: Pick<JudgeResult, "decision" | "error_category">): boolean {
  return result.decision === null && result.error_category !== null;
}

export function formatMetric(value: number | null): string {
  return value === null ? "Undefined" : new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 1 }).format(value);
}

export function confusionMatrixCells(analysis: Pick<CalibrationAnalysis, "true_positive" | "true_negative" | "false_positive" | "false_negative">) {
  return [
    { label: "True positive", human: "Passed", judge: "Passed", value: analysis.true_positive },
    { label: "False negative", human: "Passed", judge: "Failed", value: analysis.false_negative },
    { label: "False positive", human: "Failed", judge: "Passed", value: analysis.false_positive },
    { label: "True negative", human: "Failed", judge: "Failed", value: analysis.true_negative },
  ] as const;
}

export function disagreementDefinition(category: DisagreementCategory): string {
  return category === "false_positive" ? "Judge passed, human failed." : "Judge failed, human passed.";
}

export function buildDisagreementHref(runId: string, category: DisagreementCategory | undefined, offset: number): string {
  const path = `/calibration/runs/${encodeURIComponent(runId)}`;
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  if (offset > 0) params.set("offset", String(offset));
  return params.size ? `${path}?${params}` : path;
}

export function buildTraceHref(traceId: string): string {
  return `/traces/${encodeURIComponent(traceId)}`;
}

export function parseDisagreementCategory(value: string | null): DisagreementCategory | undefined {
  return value === "false_positive" || value === "false_negative" ? value : undefined;
}

export function parseOffset(value: string | null): number {
  if (!value || !/^\d+$/.test(value)) return 0;
  const offset = Number(value);
  return Number.isSafeInteger(offset) && offset <= 100_000 ? offset : 0;
}

export function comparePopulation(leftStudyId: string, rightStudyId: string): { sameStudy: boolean; warning: string | null } {
  const sameStudy = leftStudyId === rightStudyId;
  return {
    sameStudy,
    warning: sameStudy ? null : "These runs use different study snapshots/reference populations. This is not a controlled apples-to-apples comparison.",
  };
}
