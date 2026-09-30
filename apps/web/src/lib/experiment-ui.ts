import type {
  ChangeDirection,
  ExperimentConfigure,
  ExperimentRunStatus,
  ExperimentStatus,
  VariantProvenance,
} from "./experiment-api";

type ProvenanceDraft = {
  agentVersion?: string; model?: string; promptVersion?: string; workflowVersion?: string;
  gitCommitSha?: string; deploymentId?: string; metadata: string;
};
type ConfigurationDraft = {
  name: string; description: string; variantAName: string; variantBName: string;
  variantAProvenance: ProvenanceDraft; variantBProvenance: ProvenanceDraft;
  subjectText: string; evaluationDefinitionIds: string[];
};

export function experimentActions(status: ExperimentStatus) {
  return {
    canConfigure: status === "draft",
    canReady: status === "draft",
    canCreateRun: status === "ready" || status === "completed" || status === "failed",
  };
}

export function shouldPollExperimentRun(status: ExperimentRunStatus): boolean {
  return status === "queued" || status === "running";
}

export function parseSubjectPairs(value: string): {
  subjects: Array<{ a_trace_id: string; b_trace_id: string }>;
  error: string | null;
} {
  const rows = value.split(/\r?\n/).map((row) => row.trim()).filter(Boolean);
  if (!rows.length) return { subjects: [], error: "Enter at least one paired subject." };
  if (rows.length > 1_000) return { subjects: [], error: "An experiment can contain at most 1,000 paired subjects." };
  const subjects: Array<{ a_trace_id: string; b_trace_id: string }> = [];
  for (const row of rows) {
    const ids = row.split(",").map((id) => id.trim()).filter(Boolean);
    if (ids.length !== 2) return { subjects: [], error: "Each row must contain exactly two trace IDs: A, B." };
    if (ids[0] === ids[1]) return { subjects: [], error: "A and B must be distinct trace executions." };
    if (ids.some((id) => id.length > 128 || /\s/.test(id))) return { subjects: [], error: "Trace IDs must be at most 128 characters and contain no spaces." };
    subjects.push({ a_trace_id: ids[0], b_trace_id: ids[1] });
  }
  const ids = subjects.flatMap((subject) => [subject.a_trace_id, subject.b_trace_id]);
  if (new Set(ids).size !== ids.length) return { subjects: [], error: "A trace may appear only once in an experiment population." };
  return { subjects, error: null };
}

export function buildConfigurationPayload(draft: ConfigurationDraft): {
  payload: ExperimentConfigure | null; errors: Record<string, string>;
} {
  const errors: Record<string, string> = {};
  const name = draft.name.trim(); const description = draft.description.trim();
  const variantAName = draft.variantAName.trim(); const variantBName = draft.variantBName.trim();
  if (!name) errors.name = "Name is required."; else if (name.length > 200) errors.name = "Name must be 200 characters or fewer.";
  if (description.length > 2_000) errors.description = "Description must be 2,000 characters or fewer.";
  if (!variantAName) errors.variantAName = "Variant A name is required.";
  if (!variantBName) errors.variantBName = "Variant B name is required.";
  const paired = parseSubjectPairs(draft.subjectText); if (paired.error) errors.subjectText = paired.error;
  if (!draft.evaluationDefinitionIds.length) errors.evaluationDefinitionIds = "Choose at least one evaluation condition.";
  if (draft.evaluationDefinitionIds.length > 20) errors.evaluationDefinitionIds = "Choose no more than 20 evaluation conditions.";
  const a = parseProvenance(draft.variantAProvenance, "variantAProvenance", errors);
  const b = parseProvenance(draft.variantBProvenance, "variantBProvenance", errors);
  if (Object.keys(errors).length || !a || !b) return { payload: null, errors };
  return { payload: { name, description: description || null,
    variants: [{ key: "A", name: variantAName, provenance: a }, { key: "B", name: variantBName, provenance: b }],
    subjects: paired.subjects, evaluation_definition_ids: draft.evaluationDefinitionIds }, errors };
}

function parseProvenance(draft: ProvenanceDraft, key: string, errors: Record<string, string>): VariantProvenance | null {
  let metadata: unknown;
  try { metadata = draft.metadata.trim() ? JSON.parse(draft.metadata) : {}; }
  catch { errors[key] = "Custom metadata must be valid JSON."; return null; }
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) { errors[key] = "Custom metadata must be a JSON object."; return null; }
  if (new TextEncoder().encode(JSON.stringify(metadata)).length > 16_000) { errors[key] = "Custom metadata must be smaller than 16 KiB."; return null; }
  const field = (value?: string) => value?.trim() || null;
  const values = [draft.agentVersion, draft.model, draft.promptVersion, draft.workflowVersion, draft.gitCommitSha, draft.deploymentId];
  if (values.some((value) => (value?.trim().length ?? 0) > 200)) { errors[key] = "Provenance text fields must be 200 characters or fewer."; return null; }
  return { agent_version: field(draft.agentVersion), model: field(draft.model), prompt_version: field(draft.promptVersion),
    workflow_version: field(draft.workflowVersion), git_commit_sha: field(draft.gitCommitSha), deployment_id: field(draft.deploymentId),
    metadata: metadata as VariantProvenance["metadata"] };
}

export function formatProgress(completed: number, expected: number): string { return `${completed} of ${expected} decisions`; }
export function formatPercent(value: number): string { return new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 1 }).format(value); }
export function formatEffect(value: number): string { const points = value * 100; return `${points > 0 ? "+" : points < 0 ? "−" : ""}${Math.abs(points).toFixed(1)} pp`; }
export function formatOptionalNumber(value: number | null): string { return value === null ? "Undefined" : new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 }).format(value); }
export function changedDirectionLabel(direction: ChangeDirection): string { return direction === "A_PASS_B_FAIL" ? "A pass / B fail" : "A fail / B pass"; }
export function parseBoundedOffset(value: string | null): number { const parsed = Number(value ?? 0); return Number.isInteger(parsed) && parsed >= 0 && parsed <= 100_000 ? parsed : 0; }
export function parseConditionPosition(value: string | null): number | undefined { if (value === null || !/^\d+$/.test(value)) return undefined; const parsed = Number(value); return parsed <= 19 ? parsed : undefined; }
export function buildExperimentListHref(status: string, offset: number): string { const params = new URLSearchParams(); if (status) params.set("status", status); if (offset) params.set("offset", String(offset)); return `/experiments${params.size ? `?${params}` : ""}`; }
export function buildExperimentHref(id: string): string { return `/experiments/${encodeURIComponent(id)}`; }
export function buildRunResultHref(id: string, variant: string, condition: string, offset: number): string { const params = new URLSearchParams(); if (variant) params.set("variant", variant); if (condition) params.set("condition", condition); if (offset) params.set("offset", String(offset)); return `/experiments/runs/${encodeURIComponent(id)}${params.size ? `?${params}` : ""}`; }
export function buildTraceHref(id: string): string { return `/traces/${encodeURIComponent(id)}`; }
