import type {
  EvaluationDefinitionCreate,
  EvaluationOutcome,
  EvaluationRun,
  EvaluationRunStatus,
  EvaluatorKind,
} from "./evaluation-api";

export type DefinitionDraft = {
  name: string;
  description: string;
  kind: EvaluatorKind;
  expected: string;
  substring: string;
  caseSensitive: boolean;
  minimum: string;
  maximum: string;
};

export function buildDefinitionPayload(
  draft: DefinitionDraft,
): { payload: EvaluationDefinitionCreate | null; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  const name = draft.name.trim();
  const description = draft.description.trim();
  if (!name) errors.name = "Name is required.";
  if (name.length > 200) errors.name = "Name must be 200 characters or fewer.";
  if (description.length > 2_000) errors.description = "Description must be 2,000 characters or fewer.";

  let evaluatorConfig: EvaluationDefinitionCreate["evaluator_config"] = {};
  if (draft.kind === "exact_match") {
    if (!draft.expected) errors.expected = "Expected text is required.";
    evaluatorConfig = { expected: draft.expected, case_sensitive: draft.caseSensitive };
  } else if (draft.kind === "contains") {
    if (!draft.substring) errors.substring = "Required substring is required.";
    if (draft.substring.length > 4_000) errors.substring = "Substring must be 4,000 characters or fewer.";
    evaluatorConfig = { substring: draft.substring, case_sensitive: draft.caseSensitive };
  } else {
    const minimum = parseOptionalNumber(draft.minimum);
    const maximum = parseOptionalNumber(draft.maximum);
    if (minimum === "invalid" || maximum === "invalid") {
      errors.threshold = "Thresholds must be finite numbers.";
    } else if (minimum === undefined && maximum === undefined) {
      errors.threshold = "Enter a minimum, a maximum, or both.";
    } else if (minimum !== undefined && maximum !== undefined && minimum > maximum) {
      errors.threshold = "Minimum cannot exceed maximum.";
    } else {
      evaluatorConfig = {
        ...(minimum === undefined ? {} : { minimum }),
        ...(maximum === undefined ? {} : { maximum }),
      };
    }
  }

  return {
    payload: Object.keys(errors).length === 0 ? {
      name,
      description: description || null,
      evaluator_kind: draft.kind,
      evaluator_config: evaluatorConfig,
      is_enabled: true,
    } : null,
    errors,
  };
}

function parseOptionalNumber(value: string): number | undefined | "invalid" {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : "invalid";
}

export function shouldPollRun(status: EvaluationRunStatus): boolean {
  return status === "queued" || status === "running";
}

export function formatProgress(run: EvaluationRun): string {
  return `${run.result_count} / ${run.subject_count} results persisted`;
}

export function runStatusMessage(run: EvaluationRun): string {
  if (run.status === "pending") return "Select traces to submit this immutable run.";
  if (run.status === "queued") return `${formatProgress(run)} — execution is durably queued.`;
  if (run.status === "running") return `${formatProgress(run)} — evaluation running.`;
  if (run.status === "failed") return run.error_message ?? "The evaluation run failed safely.";
  return `${formatProgress(run)} — evaluation completed.`;
}

export function formatScore(score: number | null): string {
  if (score === null) return "Not scored";
  return new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 1 }).format(score);
}

export function outcomeLabel(outcome: EvaluationOutcome): string {
  return outcome === "passed" ? "Passed" : outcome === "failed" ? "Failed" : "Evaluation error";
}

export function parseOutcomeFilter(value: string | null): EvaluationOutcome | undefined {
  return value === "passed" || value === "failed" || value === "error" ? value : undefined;
}

export function parseResultOffset(value: string | null): number {
  if (!value || !/^\d+$/.test(value)) return 0;
  const offset = Number(value);
  return Number.isSafeInteger(offset) && offset <= 100_000 ? offset : 0;
}

export function buildRunResultsHref(
  runId: string,
  outcome: EvaluationOutcome | undefined,
  offset: number,
): string {
  const path = `/evaluations/runs/${encodeURIComponent(runId)}`;
  const params = new URLSearchParams();
  if (outcome) params.set("outcome", outcome);
  if (offset > 0) params.set("offset", String(offset));
  return params.size ? `${path}?${params}` : path;
}

export function buildTraceHref(traceId: string): string {
  return `/traces/${encodeURIComponent(traceId)}`;
}

export function resultEmptyMessage(outcome: EvaluationOutcome | undefined): string {
  return outcome
    ? `No ${outcome} results match this filter.`
    : "No evaluation results have been persisted yet.";
}

export function toggleTraceSelection(current: string[], traceId: string, limit = 1_000): string[] {
  if (current.includes(traceId)) return current.filter((item) => item !== traceId);
  return current.length >= limit ? current : [...current, traceId];
}

export function evaluatorKindLabel(kind: EvaluatorKind): string {
  return kind === "exact_match" ? "Exact match" : kind === "contains" ? "Contains" : "Numeric threshold";
}

export function formatConfigValue(value: unknown, maximum = 240): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (text === undefined) return "Unavailable";
  return text.length > maximum ? `${text.slice(0, maximum)}…` : text;
}
