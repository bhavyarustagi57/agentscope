import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  buildConfigurationPayload,
  buildDisagreementHref,
  buildReferenceSetPayload,
  buildStudyPayload,
  buildTraceHref,
  calibrationSections,
  comparePopulation,
  confusionMatrixCells,
  disagreementDefinition,
  formatMetric,
  isProviderFailure,
  parseTraceIds,
  referenceSetActions,
  shouldPollJudgeRun,
} from "./calibration-ui.ts";
import {
  CalibrationApiError,
  createReferenceSet,
  parseCalibrationAnalysis,
  parseJudgeProgress,
} from "./calibration-api.ts";
import { PRIMARY_NAVIGATION } from "./navigation.ts";

test("calibration is discoverable from primary navigation and exposes the four workflow sections", () => {
  assert.ok(PRIMARY_NAVIGATION.some((item) => item.label === "Calibration" && item.href === "/calibration"));
  assert.deepEqual(calibrationSections.map((section) => section.label), [
    "Reference Sets", "Studies", "Judge Configurations", "Judge Runs",
  ]);
});

test("reference-set validation trims values and enforces backend bounds", () => {
  assert.deepEqual(buildReferenceSetPayload({ name: "  Safety labels  ", description: "  Human review  " }), {
    payload: { name: "Safety labels", description: "Human review" }, errors: {},
  });
  assert.equal(buildReferenceSetPayload({ name: "", description: "" }).payload, null);
  assert.match(buildReferenceSetPayload({ name: "x".repeat(201), description: "" }).errors.name, /200/);
});

test("reference lifecycle presents legal actions and frozen immutability", () => {
  assert.deepEqual(referenceSetActions("draft", 2, 0), { canDefineSubjects: true, canBeginLabeling: true, canLabel: false, canFreeze: false });
  assert.deepEqual(referenceSetActions("labeling", 2, 2), { canDefineSubjects: false, canBeginLabeling: false, canLabel: true, canFreeze: true });
  assert.deepEqual(referenceSetActions("frozen", 2, 2), { canDefineSubjects: false, canBeginLabeling: false, canLabel: false, canFreeze: false });
});

test("study and judge configuration forms produce the exact backend contracts", () => {
  assert.deepEqual(buildStudyPayload({ name: "  Baseline  ", description: "", referenceSetId: "set-1" }).payload, {
    name: "Baseline", description: null, reference_set_id: "set-1",
  });
  const configuration = buildConfigurationPayload({
    name: "  Strict judge  ", description: "", model: "gpt-5-mini", rubric: "Return passed only when every requirement is met.",
    timeoutSeconds: "45", maxOutputTokens: "300",
  });
  assert.deepEqual(configuration.payload, {
    name: "Strict judge", description: null, provider: "openai", model: "gpt-5-mini",
    rubric: "Return passed only when every requirement is met.", output_schema_version: "1",
    timeout_seconds: 45, max_output_tokens: 300, configuration_version: "1",
  });
});

test("judge polling runs only for queued and running states", () => {
  assert.equal(shouldPollJudgeRun("pending"), false);
  assert.equal(shouldPollJudgeRun("queued"), true);
  assert.equal(shouldPollJudgeRun("running"), true);
  assert.equal(shouldPollJudgeRun("completed"), false);
  assert.equal(shouldPollJudgeRun("failed"), false);
});

test("subject input rejects empty, duplicate, and oversized populations", () => {
  assert.match(parseTraceIds("").error, /at least one/i);
  assert.match(parseTraceIds("trace-a, trace-a").error, /unique/i);
  assert.equal(parseTraceIds("trace-a\ntrace-b").traceIds.length, 2);
});

test("metrics render finite percentages while undefined stays honest", () => {
  assert.equal(formatMetric(0.875), "87.5%");
  assert.equal(formatMetric(0), "0%");
  assert.equal(formatMetric(null), "Undefined");
});

test("confusion matrix preserves human truth rows and judge prediction columns", () => {
  assert.deepEqual(confusionMatrixCells({ true_positive: 7, true_negative: 5, false_positive: 2, false_negative: 3 }), [
    { label: "True positive", human: "Passed", judge: "Passed", value: 7 },
    { label: "False negative", human: "Passed", judge: "Failed", value: 3 },
    { label: "False positive", human: "Failed", judge: "Passed", value: 2 },
    { label: "True negative", human: "Failed", judge: "Failed", value: 5 },
  ]);
});

test("disagreement categories explain false positives and false negatives", () => {
  assert.match(disagreementDefinition("false_positive"), /judge passed.*human failed/i);
  assert.match(disagreementDefinition("false_negative"), /judge failed.*human passed/i);
});

test("disagreement links preserve filters and safely encode trace navigation", () => {
  assert.equal(buildDisagreementHref("run/id", "false_positive", 20), "/calibration/runs/run%2Fid?category=false_positive&offset=20");
  assert.equal(buildTraceHref("trace/with spaces"), "/traces/trace%2Fwith%20spaces");
});

test("comparison identifies controlled and different-population cases without ranking", () => {
  assert.deepEqual(comparePopulation("study-1", "study-1"), { sameStudy: true, warning: null });
  const different = comparePopulation("study-1", "study-2");
  assert.equal(different.sameStudy, false);
  assert.match(different.warning, /not a controlled apples-to-apples comparison/i);
});

test("analysis parsing preserves null metrics instead of coercing them", () => {
  const parsed = parseCalibrationAnalysis({
    run_id: "run-1", study_id: "study-1", metric_schema_version: "1", created_at: "2026-09-23T00:00:00Z",
    sample_count: 1, human_passed_count: 1, human_failed_count: 0, judge_passed_count: 1, judge_failed_count: 0,
    agreement_count: 1, disagreement_count: 0, true_positive: 1, true_negative: 0, false_positive: 0, false_negative: 0,
    observed_agreement: 1, expected_agreement: 1, precision_passed: 1, recall_passed: 1, f1_passed: 1,
    specificity_failed: null, cohens_kappa: null, kappa_is_defined: false,
    undefined_metrics: ["specificity_failed", "cohens_kappa"],
  });
  assert.equal(parsed.specificity_failed, null);
  assert.equal(parsed.cohens_kappa, null);
});

test("progress parsing keeps provider failures separate from decisions", () => {
  const progress = parseJudgeProgress({
    run_id: "run-1", status: "running", subject_count: 4, result_count: 3,
    passed_count: 1, failed_count: 1, error_count: 1, pending_count: 1,
  });
  assert.equal(progress.error_count, 1);
  assert.equal(progress.passed_count + progress.failed_count, 2);
  assert.equal(isProviderFailure({ decision: null, error_category: "provider_timeout" }), true);
  assert.equal(isProviderFailure({ decision: "failed", error_category: null }), false);
});

test("workspace source includes intentional loading, error, and accessible workflow states", async () => {
  const source = await readFile(new URL("../components/calibration/calibration-workspace.tsx", import.meta.url), "utf8");
  assert.match(source, /aria-label="Calibration workflow"/);
  assert.match(source, /Loading calibration workspace/);
  assert.match(source, /Couldn’t load calibration/);
});

test("reference UX explicitly distinguishes raw annotation and authoritative label", async () => {
  const source = await readFile(new URL("../components/calibration/reference-sets-panel.tsx", import.meta.url), "utf8");
  assert.match(source, /Save raw annotation/);
  assert.match(source, /Set authoritative label/);
  assert.match(source, /This set is frozen/);
});

test("run detail exposes provider failures separately and accessible report controls", async () => {
  const source = await readFile(new URL("../components/calibration/calibration-run-detail.tsx", import.meta.url), "utf8");
  assert.match(source, /Provider and execution failures/);
  assert.match(source, /Create calibration analysis/);
  assert.match(source, /aria-live="polite"/);
});

test("disagreement explorer renders trace navigation, rationale, and empty state", async () => {
  const source = await readFile(new URL("../components/calibration/calibration-report.tsx", import.meta.url), "utf8");
  assert.match(source, /Inspect trace/);
  assert.match(source, /Judge rationale/);
  assert.match(source, /No disagreements/);
});

test("reference-set creation uses typed JSON and safe API errors", async (t) => {
  const requests = [];
  t.mock.method(globalThis, "fetch", async (input, init) => {
    requests.push({ input: String(input), body: JSON.parse(String(init?.body)) });
    return Response.json({
      id: "set-1", name: "Safety labels", description: null, status: "draft",
      created_at: "2026-09-23T00:00:00Z", updated_at: "2026-09-23T00:00:00Z", frozen_at: null,
      subject_count: 0, annotation_count: 0, reference_count: 0, unlabeled_count: 0, passed_count: 0, failed_count: 0,
    }, { status: 201 });
  });
  const created = await createReferenceSet({ name: "Safety labels", description: null });
  assert.equal(created.status, "draft");
  assert.deepEqual(requests[0].body, { name: "Safety labels", description: null });

  t.mock.method(globalThis, "fetch", async () => Response.json({
    error: { code: "REFERENCE_SET_CONFLICT", message: "operation conflicts with the reference set lifecycle" },
  }, { status: 409 }));
  await assert.rejects(
    createReferenceSet({ name: "Blocked", description: null }),
    (error) => error instanceof CalibrationApiError && error.status === 409 && error.code === "REFERENCE_SET_CONFLICT",
  );
});
