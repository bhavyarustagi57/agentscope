import type {
  BisectionAnalysisStatus,
  BisectionAnalysisStep,
  ExecutionOutcome,
  ProbeConfiguration,
  RegressionCheck,
  RegressionClassification,
  VariantKey,
} from "./regression-api";

type PolicyFields = {
  name: string;
  description: string;
  minimumPassRateDrop: string;
  minimumSampleSize: string;
};

type ProbeFields = {
  executable: string;
  argumentsText: string;
  workingDirectory: string;
  timeoutSeconds: string;
  maxStdoutBytes: string;
  maxStderrBytes: string;
};

export function buildPolicyPayload(fields: PolicyFields) {
  const errors: Record<string, string> = {};
  const name = fields.name.trim();
  const description = fields.description.trim();
  const drop = Number(fields.minimumPassRateDrop);
  const sampleSize = Number(fields.minimumSampleSize);
  if (!name) errors.name = "Policy name is required.";
  else if (name.length > 200) errors.name = "Policy name must be 200 characters or fewer.";
  if (description.length > 2_000) errors.description = "Description must be 2,000 characters or fewer.";
  if (!Number.isFinite(drop) || drop <= 0 || drop > 1) errors.minimumPassRateDrop = "Pass-rate drop must be between 0 (exclusive) and 1.";
  if (!Number.isInteger(sampleSize) || sampleSize < 1 || sampleSize > 1_000) errors.minimumSampleSize = "Minimum sample size must be between 1 and 1,000.";
  return {
    errors,
    payload: {
      name,
      description: description || null,
      minimum_pass_rate_drop: drop,
      minimum_sample_size: sampleSize,
    },
  };
}

export function buildCheckPayload(
  experimentRunId: string,
  regressionPolicyId: string,
  baselineVariant: VariantKey | "",
  candidateVariant: VariantKey | "",
) {
  if (!experimentRunId.trim() || !regressionPolicyId.trim()) return { payload: null, error: "Choose an experiment run and regression policy." };
  if (!baselineVariant || !candidateVariant) return { payload: null, error: "Choose both a baseline and candidate variant." };
  if (baselineVariant === candidateVariant) return { payload: null, error: "Baseline and candidate variants must differ." };
  return {
    payload: {
      experiment_run_id: experimentRunId.trim(),
      regression_policy_id: regressionPolicyId.trim(),
      baseline_variant: baselineVariant,
      candidate_variant: candidateVariant,
    },
    error: null,
  };
}

export function parseProbeArguments(value: string): string[] {
  return value.split(/\r?\n/).filter((argument) => argument.length > 0);
}

export function buildProbeConfiguration(fields: ProbeFields) {
  const errors: Record<string, string> = {};
  const executable = fields.executable.trim();
  const workingDirectory = fields.workingDirectory.trim() || ".";
  const args = parseProbeArguments(fields.argumentsText);
  const timeout = Number(fields.timeoutSeconds);
  const stdout = Number(fields.maxStdoutBytes);
  const stderr = Number(fields.maxStderrBytes);
  if (!executable) errors.executable = "Executable is required.";
  else if (executable.length > 500 || /[\r\n\0]/.test(executable)) errors.executable = "Executable must be one argv value of 500 characters or fewer.";
  if (args.length > 64 || args.some((argument) => argument.length > 1_000 || argument.includes("\0"))) errors.argumentsText = "Provide at most 64 arguments, each no longer than 1,000 characters.";
  const pathParts = workingDirectory.replaceAll("\\", "/").split("/");
  if (/^(?:[a-zA-Z]:|\/)/.test(workingDirectory) || pathParts.includes("..") || workingDirectory.includes("\0")) errors.workingDirectory = "Working directory must remain relative to the repository root.";
  if (!boundedInteger(timeout, 1, 3_600)) errors.timeoutSeconds = "Timeout must be between 1 and 3,600 seconds.";
  if (!boundedInteger(stdout, 1, 1_048_576)) errors.maxStdoutBytes = "Stdout limit must be between 1 and 1,048,576 bytes.";
  if (!boundedInteger(stderr, 1, 1_048_576)) errors.maxStderrBytes = "Stderr limit must be between 1 and 1,048,576 bytes.";
  const configuration: ProbeConfiguration = {
    executable,
    args,
    working_directory: workingDirectory,
    timeout_seconds: timeout,
    max_stdout_bytes: stdout,
    max_stderr_bytes: stderr,
  };
  return { errors, configuration };
}

export function shouldPollBisectionAnalysis(status: BisectionAnalysisStatus): boolean {
  return status === "queued" || status === "running" || status === "waiting";
}

export function bisectionEligibility(check: RegressionCheck): { eligible: boolean; reason: string | null } {
  if (check.classification !== "regression_detected") return { eligible: false, reason: "Git bisection requires a regression-detected check." };
  if (!gitSha(check.baseline_provenance) || !gitSha(check.candidate_provenance)) return { eligible: false, reason: "Both variants need Git provenance before bisection planning." };
  return { eligible: true, reason: null };
}

export function classificationLabel(value: RegressionClassification): string {
  return { regression_detected: "Regression detected", no_regression_detected: "No regression detected", insufficient_evidence: "Insufficient evidence" }[value];
}

export function analysisOutcomeLabel(value: BisectionAnalysisStatus): string {
  return value === "inconclusive" ? "Inconclusive" : value === "failed" ? "Analysis failed" : value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

export function executionOutcomeLabel(value: ExecutionOutcome): string {
  return value === "execution_failed" ? "EXECUTION FAILED" : value.toUpperCase();
}

export function evidenceSourceLabel(value: "reused" | "requested"): string {
  return value === "reused" ? "Reused compatible evidence" : "New execution requested";
}

export function terminalReasonLabel(value: string | null): string {
  if (!value) return "Not provided";
  return value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

export function formatPassRateDrop(value: number): string {
  return `${Number((value * 100).toFixed(2))} percentage points`;
}

export function formatOptionalMetric(value: number | null, maximumFractionDigits = 4): string {
  return value === null ? "Not available" : new Intl.NumberFormat("en-US", { maximumFractionDigits }).format(value);
}

export function formatEffect(value: number | null): string {
  if (value === null) return "Not available";
  const points = value * 100;
  return `${points > 0 ? "+" : points < 0 ? "−" : ""}${Math.abs(points).toFixed(1)} pp`;
}

export function shortSha(value: string): string {
  return value.length > 12 ? value.slice(0, 12) : value;
}

export function sortAnalysisSteps(steps: BisectionAnalysisStep[]): BisectionAnalysisStep[] {
  return [...steps].sort((left, right) => left.sequence_number - right.sequence_number);
}

function boundedInteger(value: number, minimum: number, maximum: number): boolean {
  return Number.isInteger(value) && value >= minimum && value <= maximum;
}

function gitSha(provenance: Record<string, unknown>): string | null {
  const value = provenance.git_commit_sha;
  return typeof value === "string" && /^[0-9a-f]{40}$/.test(value) ? value : null;
}
