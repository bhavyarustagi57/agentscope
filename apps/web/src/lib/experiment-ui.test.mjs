import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  buildConfigurationPayload,
  buildExperimentHref,
  buildExperimentListHref,
  buildRunResultHref,
  buildTraceHref,
  changedDirectionLabel,
  experimentActions,
  formatEffect,
  formatOptionalNumber,
  formatProgress,
  parseBoundedOffset,
  parseConditionPosition,
  parseSubjectPairs,
  shouldPollExperimentRun,
} from "./experiment-ui.ts";
import { listExperimentResults, parseExperimentAnalysis, parseExperimentRun } from "./experiment-api.ts";
import { PRIMARY_NAVIGATION } from "./navigation.ts";

test("experiments are discoverable from primary navigation", () => {
  assert.ok(PRIMARY_NAVIGATION.some((item) => item.label === "Experiments" && item.href === "/experiments"));
});

test("lifecycle controls make ready configuration read-only", () => {
  assert.deepEqual(experimentActions("draft"), { canConfigure: true, canReady: true, canCreateRun: false });
  assert.deepEqual(experimentActions("ready"), { canConfigure: false, canReady: false, canCreateRun: true });
  assert.deepEqual(experimentActions("running"), { canConfigure: false, canReady: false, canCreateRun: false });
  assert.deepEqual(experimentActions("completed"), { canConfigure: false, canReady: false, canCreateRun: true });
});

test("paired-subject input preserves ordered A/B identity and rejects invalid pairs", () => {
  assert.deepEqual(parseSubjectPairs("a-1, b-1\na-2, b-2"), {
    subjects: [{ a_trace_id: "a-1", b_trace_id: "b-1" }, { a_trace_id: "a-2", b_trace_id: "b-2" }],
    error: null,
  });
  assert.match(parseSubjectPairs("same, same").error, /distinct/i);
  assert.match(parseSubjectPairs("a-1, b-1\na-1, b-2").error, /only once/i);
  assert.match(parseSubjectPairs("a-only").error, /two trace IDs/i);
});

test("configuration builder trims bounded fields and sends exact server orientation", () => {
  const result = buildConfigurationPayload({
    name: "  Retry experiment  ", description: "  paired evidence  ",
    variantAName: "Baseline", variantBName: "Candidate",
    variantAProvenance: { model: " gpt-a ", metadata: "{\"temperature\":0}" },
    variantBProvenance: { model: " gpt-b ", metadata: "{}" },
    subjectText: "trace-a, trace-b", evaluationDefinitionIds: ["definition-1"],
  });
  assert.deepEqual(result.errors, {});
  assert.equal(result.payload.variants[0].key, "A");
  assert.equal(result.payload.variants[1].key, "B");
  assert.deepEqual(result.payload.subjects[0], { a_trace_id: "trace-a", b_trace_id: "trace-b" });
  assert.equal(result.payload.variants[0].provenance.model, "gpt-a");
  assert.deepEqual(result.payload.variants[0].provenance.metadata, { temperature: 0 });
  const oversized = buildConfigurationPayload({
    name: "Experiment", description: "", variantAName: "A", variantBName: "B",
    variantAProvenance: { metadata: JSON.stringify({ value: "x".repeat(17_000) }) },
    variantBProvenance: { metadata: "{}" }, subjectText: "a, b", evaluationDefinitionIds: ["def"],
  });
  assert.match(oversized.errors.variantAProvenance, /16 KiB/i);
});

test("run polling and progress match backend lifecycle and decision counts", () => {
  assert.equal(shouldPollExperimentRun("pending"), false);
  assert.equal(shouldPollExperimentRun("queued"), true);
  assert.equal(shouldPollExperimentRun("running"), true);
  assert.equal(shouldPollExperimentRun("completed"), false);
  assert.equal(shouldPollExperimentRun("failed"), false);
  assert.equal(formatProgress(3, 8), "3 of 8 decisions");
});

test("statistical formatting preserves B-A orientation and undefined values", () => {
  assert.equal(formatEffect(0.125), "+12.5 pp");
  assert.equal(formatEffect(-0.05), "−5.0 pp");
  assert.equal(formatOptionalNumber(null), "Undefined");
  assert.equal(formatOptionalNumber(1.25), "1.25");
});

test("changed-subject directions remain neutral", () => {
  assert.equal(changedDirectionLabel("A_PASS_B_FAIL"), "A pass / B fail");
  assert.equal(changedDirectionLabel("A_FAIL_B_PASS"), "A fail / B pass");
});

test("pagination and trace links encode identifiers and retain filters", () => {
  assert.equal(buildExperimentListHref("ready", 20), "/experiments?status=ready&offset=20");
  assert.equal(buildExperimentHref("exp/id"), "/experiments/exp%2Fid");
  assert.equal(buildRunResultHref("run/id", "B", "2", 40), "/experiments/runs/run%2Fid?variant=B&condition=2&offset=40");
  assert.equal(buildRunResultHref("run/id", "", "2", 40), "/experiments/runs/run%2Fid?condition=2&offset=40");
  assert.equal(buildTraceHref("trace/with spaces"), "/traces/trace%2Fwith%20spaces");
  assert.equal(parseBoundedOffset("100000"), 100000);
  assert.equal(parseBoundedOffset("100001"), 0);
  assert.equal(parseConditionPosition("19"), 19);
  assert.equal(parseConditionPosition("20"), undefined);
});

test("raw-result queries omit the combined variant and compose filters with pagination", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (input) => {
    requests.push(String(input));
    return new Response(JSON.stringify({ items: [], has_more: false }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  try {
    await listExperimentResults("run/id");
    await listExperimentResults("run/id", { variant: "A" });
    await listExperimentResults("run/id", { variant: "B" });
    await listExperimentResults("run/id", { condition: 2, offset: 40 });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.deepEqual(requests, [
    "http://localhost:8000/api/v1/experiment-runs/run%2Fid/results?page_size=20&offset=0",
    "http://localhost:8000/api/v1/experiment-runs/run%2Fid/results?page_size=20&offset=0&variant=A",
    "http://localhost:8000/api/v1/experiment-runs/run%2Fid/results?page_size=20&offset=0&variant=B",
    "http://localhost:8000/api/v1/experiment-runs/run%2Fid/results?page_size=20&offset=40&condition_position=2",
  ]);
});

test("API parser keeps nullable odds ratios and explicit ineligible conditions", () => {
  const analysis = parseExperimentAnalysis({
    id: "analysis-1", run_id: "run-1", experiment_id: "exp-1", analysis_schema_version: "1",
    confidence_method: "paired_percentile_bootstrap", confidence_level: 0.95, bootstrap_seed: 6003,
    bootstrap_iterations: 10000, hypothesis_test_method: "exact_two_sided_mcnemar", alpha: 0.05,
    created_at: "2026-09-23T00:00:00Z", conditions: [
      { condition_position: 0, definition_id: "def-1", definition_name: "Exact", evaluator_kind: "exact_match", result: {
        eligible: true, ineligible_reason: null, sample_size: 2, a_passed_count: 1, a_failed_count: 1,
        b_passed_count: 2, b_failed_count: 0, a_pass_rate: 0.5, b_pass_rate: 1,
        pass_rate_difference: 0.5, both_passed_count: 1, both_passed_rate: 0.5,
        both_failed_count: 0, both_failed_rate: 0, a_only_passed_count: 0, a_only_passed_rate: 0,
        b_only_passed_count: 1, b_only_passed_rate: 0.5, matched_pairs_odds_ratio: null,
        confidence_level: 0.95, confidence_interval_lower: 0, confidence_interval_upper: 1,
        confidence_method: "paired_percentile_bootstrap", test_method: "exact_two_sided_mcnemar",
        discordant_count: 1, test_statistic: null, p_value: 1, alpha: 0.05, rejects_null: false,
      } },
      { condition_position: 1, definition_id: "def-2", definition_name: "Numeric", evaluator_kind: "numeric_threshold",
        result: { eligible: false, ineligible_reason: "non_binary_outcome" } },
    ],
  });
  assert.equal(analysis.conditions[0].result.matched_pairs_odds_ratio, null);
  assert.deepEqual(analysis.conditions[1].result, { eligible: false, ineligible_reason: "non_binary_outcome" });
});

test("run parser rejects contradictory progress", () => {
  assert.throws(() => parseExperimentRun({
    id: "run-1", experiment_id: "exp-1", status: "running", expected_decision_count: 2,
    completed_decision_count: 3, evaluator_error_count: 0, remaining_decision_count: 0,
    error_category: null, error_message: null, created_at: "2026-09-23T00:00:00Z",
    queued_at: null, started_at: null, completed_at: null, attempt_count: 1,
  }), /unexpected response/i);
});

test("workspace source includes honest report guidance and ready freeze language", async () => {
  const detail = await readFile(new URL("../components/experiments/experiment-detail.tsx", import.meta.url), "utf8");
  const report = await readFile(new URL("../components/experiments/experiment-report.tsx", import.meta.url), "utf8");
  assert.match(detail, /freezes variants, provenance, subject ordering, and evaluation conditions/i);
  assert.match(report, /Failure to reject does not establish equivalence/);
  assert.doesNotMatch(report, /winner|loser|ship B|better variant|recommended variant|trust score/i);
});
