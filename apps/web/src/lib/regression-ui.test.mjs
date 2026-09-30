import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  analysisOutcomeLabel,
  bisectionEligibility,
  buildCheckPayload,
  buildPolicyPayload,
  buildProbeConfiguration,
  classificationLabel,
  evidenceSourceLabel,
  executionOutcomeLabel,
  formatEffect,
  formatOptionalMetric,
  formatPassRateDrop,
  parseProbeArguments,
  shortSha,
  shouldPollBisectionAnalysis,
  sortAnalysisSteps,
  terminalReasonLabel,
} from "./regression-ui.ts";
import {
  createBisectionAnalysis,
  parseBisectionAnalysis,
  parseRegressionCheck,
  startBisectionAnalysis,
} from "./regression-api.ts";
import { PRIMARY_NAVIGATION } from "./navigation.ts";

const sha = (digit) => digit.repeat(40);

test("regressions are exposed by one primary navigation item", () => {
  assert.equal(PRIMARY_NAVIGATION.filter((item) => item.label === "Regressions").length, 1);
  assert.ok(PRIMARY_NAVIGATION.some((item) => item.href === "/regressions"));
});

test("regression policy builder trims values and explains fractional thresholds", () => {
  const result = buildPolicyPayload({
    name: "  Release guard  ",
    description: "  five point drop  ",
    minimumPassRateDrop: "0.05",
    minimumSampleSize: "20",
  });
  assert.deepEqual(result.errors, {});
  assert.deepEqual(result.payload, {
    name: "Release guard",
    description: "five point drop",
    minimum_pass_rate_drop: 0.05,
    minimum_sample_size: 20,
  });
  assert.equal(formatPassRateDrop(0.05), "5 percentage points");
});

test("regression policy builder enforces backend bounds", () => {
  assert.match(buildPolicyPayload({ name: "", description: "", minimumPassRateDrop: "0", minimumSampleSize: "0" }).errors.name, /required/i);
  assert.match(buildPolicyPayload({ name: "Policy", description: "", minimumPassRateDrop: "1.1", minimumSampleSize: "1001" }).errors.minimumPassRateDrop, /between/i);
});

test("regression check payload preserves explicit baseline and candidate orientation", () => {
  assert.deepEqual(buildCheckPayload("run-1", "policy-1", "B", "A"), {
    payload: { experiment_run_id: "run-1", regression_policy_id: "policy-1", baseline_variant: "B", candidate_variant: "A" },
    error: null,
  });
});

test("regression check rejects selecting the same variant twice", () => {
  assert.match(buildCheckPayload("run-1", "policy-1", "A", "A").error, /must differ/i);
});

test("regression classifications remain distinct and neutral", () => {
  assert.equal(classificationLabel("regression_detected"), "Regression detected");
  assert.equal(classificationLabel("no_regression_detected"), "No regression detected");
  assert.equal(classificationLabel("insufficient_evidence"), "Insufficient evidence");
});

test("regression check parser preserves insufficient evidence and null metrics", () => {
  const check = parseRegressionCheck(regressionCheckFixture("insufficient_evidence", null));
  assert.equal(check.classification, "insufficient_evidence");
  assert.equal(check.findings[0].sample_size, null);
  assert.equal(check.findings[0].p_value, null);
});

test("null metrics render as unavailable instead of zero", () => {
  assert.equal(formatOptionalMetric(null), "Not available");
  assert.equal(formatOptionalMetric(0), "0");
  assert.equal(formatEffect(null), "Not available");
});

test("completed experiment run source exposes the Check for regression entry path", async () => {
  const source = await readFile(new URL("../components/experiments/experiment-run-detail.tsx", import.meta.url), "utf8");
  assert.match(source, /Check for regression/);
  assert.match(source, /runId=/);
});

test("bisection eligibility requires detected regression and both Git SHAs", () => {
  assert.equal(bisectionEligibility(parseRegressionCheck(regressionCheckFixture("regression_detected", sha("a")))).eligible, true);
});

test("missing Git provenance produces an explicit ineligible state", () => {
  const result = bisectionEligibility(parseRegressionCheck(regressionCheckFixture("regression_detected", null)));
  assert.equal(result.eligible, false);
  assert.match(result.reason, /Git provenance/i);
});

test("probe arguments are newline-delimited argv values without shell parsing", () => {
  assert.deepEqual(parseProbeArguments("scripts/probe.py\n--label=value with spaces\n&& literal"), [
    "scripts/probe.py",
    "--label=value with spaces",
    "&& literal",
  ]);
});

test("probe builder sends exact Prompt 3 fields and no shell command", () => {
  const result = buildProbeConfiguration({ executable: "python", argumentsText: "scripts/probe.py\n--strict", workingDirectory: ".", timeoutSeconds: "30", maxStdoutBytes: "4096", maxStderrBytes: "8192" });
  assert.deepEqual(result.errors, {});
  assert.deepEqual(result.configuration, { executable: "python", args: ["scripts/probe.py", "--strict"], working_directory: ".", timeout_seconds: 30, max_stdout_bytes: 4096, max_stderr_bytes: 8192 });
  assert.equal("command" in result.configuration, false);
});

test("probe builder validates working-directory containment and numeric bounds", () => {
  const result = buildProbeConfiguration({ executable: "", argumentsText: "", workingDirectory: "../outside", timeoutSeconds: "0", maxStdoutBytes: "0", maxStderrBytes: "1048577" });
  assert.match(result.errors.executable, /required/i);
  assert.match(result.errors.workingDirectory, /repository/i);
  assert.match(result.errors.timeoutSeconds, /between/i);
});

test("analysis API creates and starts using separate typed requests", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (input, init = {}) => {
    requests.push({ url: String(input), method: init.method, body: init.body });
    if (String(input).endsWith("/execute")) return Response.json({ analysis_id: "analysis-1", status: "queued", queue_delivery: "enqueued" }, { status: 202 });
    return Response.json(analysisFixture("pending"), { status: 201 });
  };
  try {
    await createBisectionAnalysis("session/id", { executable: "python", args: [], working_directory: ".", timeout_seconds: 30, max_stdout_bytes: 4096, max_stderr_bytes: 4096 });
    await startBisectionAnalysis("analysis-1");
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.deepEqual(requests.map(({ url, method }) => ({ url, method })), [
    { url: "http://localhost:8000/api/v1/bisection-sessions/session%2Fid/analyses", method: "POST" },
    { url: "http://localhost:8000/api/v1/bisection-analyses/analysis-1/execute", method: "POST" },
  ]);
  assert.deepEqual(JSON.parse(requests[0].body), { configuration: { executable: "python", args: [], working_directory: ".", timeout_seconds: 30, max_stdout_bytes: 4096, max_stderr_bytes: 4096 } });
});

test("analysis polling is active-only", () => {
  for (const status of ["queued", "running", "waiting"]) assert.equal(shouldPollBisectionAnalysis(status), true);
  for (const status of ["pending", "attributed", "inconclusive", "failed"]) assert.equal(shouldPollBisectionAnalysis(status), false);
});

test("terminal analysis parsing preserves attributed boundary snapshots", () => {
  const analysis = parseBisectionAnalysis(analysisFixture("attributed"));
  assert.equal(analysis.final_good_commit_sha, sha("b"));
  assert.equal(analysis.final_bad_commit_sha, sha("c"));
  assert.equal(analysis.final_bad_snapshot?.subject, "first observed regression");
});

test("inconclusive and failed statuses have distinct display language", () => {
  assert.equal(analysisOutcomeLabel("inconclusive"), "Inconclusive");
  assert.equal(analysisOutcomeLabel("failed"), "Analysis failed");
  assert.equal(terminalReasonLabel("execution_failure_gap"), "Execution failure gap");
  assert.equal(terminalReasonLabel("inconsistent_evidence"), "Inconsistent evidence");
});

test("decision timeline ordering follows persisted sequence numbers", () => {
  const steps = analysisFixture("running").steps;
  assert.deepEqual(sortAnalysisSteps([steps[1], steps[0]]).map((step) => step.sequence_number), [1, 2]);
});

test("reused and newly requested evidence remain explicit", () => {
  assert.equal(evidenceSourceLabel("reused"), "Reused compatible evidence");
  assert.equal(evidenceSourceLabel("requested"), "New execution requested");
});

test("all Prompt 3 outcomes remain distinct", () => {
  assert.equal(executionOutcomeLabel("pass"), "PASS");
  assert.equal(executionOutcomeLabel("regression"), "REGRESSION");
  assert.equal(executionOutcomeLabel("indeterminate"), "INDETERMINATE");
  assert.equal(executionOutcomeLabel("execution_failed"), "EXECUTION FAILED");
});

test("SHA formatting abbreviates visually without altering the full value", () => {
  assert.equal(shortSha(sha("d")), "dddddddddddd");
  assert.equal(shortSha("short"), "short");
});

test("regression check page renders persisted metrics and Git planning errors", async () => {
  const source = await readFile(new URL("../components/regressions/regression-check-detail.tsx", import.meta.url), "utf8");
  assert.match(source, /McNemar p-value/);
  assert.match(source, /candidate.minus.baseline/i);
  assert.match(source, /role="alert"/);
  assert.match(source, /no statistics are calculated or recomputed in this browser/i);
});

test("bisection page renders bounded execution evidence and terminal states", async () => {
  const source = await readFile(new URL("../components/regressions/bisection-session-detail.tsx", import.meta.url), "utf8");
  assert.match(source, /stdout.*truncated|truncated.*stdout/is);
  assert.match(source, /stderr.*truncated|truncated.*stderr/is);
  assert.match(source, /Previous known-good commit/);
  assert.match(source, /First observed regressed commit/);
  assert.match(source, /does not by itself prove causal root cause/i);
  assert.match(source, /Inconclusive/);
  assert.match(source, /Analysis failed/);
});

test("Phase 7 layouts include responsive wrapping for SHA and path values", async () => {
  const sources = await Promise.all([
    readFile(new URL("../components/regressions/regressions-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/regressions/regression-check-detail.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/regressions/bisection-session-detail.tsx", import.meta.url), "utf8"),
  ]);
  assert.ok(sources.every((source) => /sm:|lg:/.test(source)));
  assert.ok(sources.every((source) => /break-all|break-words|truncate/.test(source)));
});

function regressionCheckFixture(classification, gitSha) {
  return {
    id: "check-1", experiment_run_id: "run-1", experiment_id: "experiment-1", analysis_id: "experiment-analysis-1",
    regression_policy_id: "policy-1", baseline_variant: "A", candidate_variant: "B", classification,
    policy_name: "Release guard", policy_description: null, minimum_pass_rate_drop: 0.05, minimum_sample_size: 20,
    baseline_provenance: { git_commit_sha: gitSha }, candidate_provenance: { git_commit_sha: gitSha ? sha("f") : null },
    created_at: "2026-09-24T00:00:00Z", findings: [{ condition_position: 0, analysis_id: "experiment-analysis-1", definition_id: "definition-1",
      definition_name: "Correctness", evaluator_kind: "exact_match", evaluator_config: {}, baseline_variant: "A", candidate_variant: "B",
      source_eligible: classification !== "insufficient_evidence", source_ineligible_reason: classification === "insufficient_evidence" ? "non_binary_outcome" : null,
      classification, minimum_pass_rate_drop: 0.05, minimum_sample_size: 20, sample_size: classification === "insufficient_evidence" ? null : 25,
      baseline_passed_count: 24, candidate_passed_count: 20, baseline_pass_rate: 0.96, candidate_pass_rate: 0.8,
      candidate_minus_baseline: -0.16, both_passed_count: 20, both_failed_count: 1, baseline_only_passed_count: 4,
      candidate_only_passed_count: 0, discordant_count: 4, matched_pairs_odds_ratio: null, p_value: classification === "insufficient_evidence" ? null : 0.125,
      rejects_null: false, source_interval_lower: -0.3, source_interval_upper: -0.02, confidence_interval_lower: -0.3,
      confidence_interval_upper: -0.02, created_at: "2026-09-24T00:00:00Z" }],
  };
}

function analysisFixture(status) {
  const terminal = ["attributed", "inconclusive", "failed"].includes(status);
  const step = (sequence, outcome) => ({ sequence_number: sequence, good_position_before: -1, good_commit_sha_before: sha("a"),
    bad_position_before: 4, bad_commit_sha_before: sha("f"), selected_position: sequence, selected_commit_sha: sha(String(sequence)),
    evidence_source: sequence === 1 ? "reused" : "requested", execution_run_id: `run-${sequence}`, observed_outcome: outcome,
    decision: outcome === "pass" ? "advance_good" : "retreat_bad", good_position_after: outcome === "pass" ? sequence : -1,
    good_commit_sha_after: outcome === "pass" ? sha(String(sequence)) : sha("a"), bad_position_after: outcome === "regression" ? sequence : 4,
    bad_commit_sha_after: outcome === "regression" ? sha(String(sequence)) : sha("f"), created_at: "2026-09-24T00:00:00Z" });
  return { id: "analysis-1", session_id: "session-1", status, configuration_schema_version: "1",
    configuration: { executable: "python", args: ["probe.py"], working_directory: ".", timeout_seconds: 30, max_stdout_bytes: 4096, max_stderr_bytes: 4096 },
    repository_fingerprint: sha("e").slice(0, 64), baseline_commit_sha: sha("a"), candidate_commit_sha: sha("f"), total_commit_count: 5,
    current_interval_size: status === "attributed" ? 0 : 2, good_position: status === "attributed" ? 1 : -1, good_commit_sha: status === "attributed" ? sha("b") : sha("a"),
    bad_position: status === "attributed" ? 2 : 4, bad_commit_sha: status === "attributed" ? sha("c") : sha("f"), step_count: 2,
    reused_evidence_count: 1, new_evidence_count: 1, usable_evidence_count: 2, indeterminate_count: 0, execution_failure_count: 0,
    final_good_commit_sha: status === "attributed" ? sha("b") : null, final_good_position: status === "attributed" ? 1 : null,
    final_bad_commit_sha: status === "attributed" ? sha("c") : null, final_bad_position: status === "attributed" ? 2 : null,
    final_good_snapshot: status === "attributed" ? { sha: sha("b"), position: 1, subject: "last known good", committed_at: "2026-09-24T00:00:00Z", parent_shas: [sha("a")] } : null,
    final_bad_snapshot: status === "attributed" ? { sha: sha("c"), position: 2, subject: "first observed regression", committed_at: "2026-09-24T00:00:00Z", parent_shas: [sha("b")] } : null,
    terminal_reason: status === "inconclusive" ? "inconsistent_evidence" : status === "failed" ? "orchestration_failed" : null,
    terminal_message: status === "failed" ? "Analysis could not proceed." : null, created_at: "2026-09-24T00:00:00Z", queued_at: null,
    started_at: null, completed_at: terminal ? "2026-09-24T00:01:00Z" : null, steps: [step(1, "pass"), step(2, "regression")],
  };
}
