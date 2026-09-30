import assert from "node:assert/strict";
import test from "node:test";

import {
  buildDefinitionPayload,
  buildRunResultsHref,
  buildTraceHref,
  formatProgress,
  formatScore,
  outcomeLabel,
  parseOutcomeFilter,
  parseResultOffset,
  resultEmptyMessage,
  runStatusMessage,
  shouldPollRun,
  toggleTraceSelection,
} from "./evaluation-ui.ts";
import {
  EvaluationApiError,
  createEvaluationDefinition,
  createEvaluationRun,
  executeEvaluationRun,
  parseEvaluationRun,
} from "./evaluation-api.ts";
import { PRIMARY_NAVIGATION } from "./navigation.ts";

const baseRun = {
  id: "11111111-1111-1111-1111-111111111111",
  definition_id: "22222222-2222-2222-2222-222222222222",
  definition_name: "Exact response",
  evaluator_kind: "exact_match",
  evaluator_config: { expected: "Paris", case_sensitive: true },
  status: "pending",
  error_message: null,
  created_at: "2026-09-18T10:00:00Z",
  queued_at: null,
  started_at: null,
  completed_at: null,
  subject_count: 0,
  result_count: 0,
  passed_count: 0,
  failed_count: 0,
  error_count: 0,
  scored_count: 0,
  average_score: null,
};

test("primary navigation exposes the evaluations product route", () => {
  assert.ok(PRIMARY_NAVIGATION.some((item) => item.label === "Evaluations" && item.href === "/evaluations"));
});

test("definition payload builder produces evaluator-specific contracts", () => {
  const exact = buildDefinitionPayload({
    name: "  Final answer  ",
    description: "  Literal expected value  ",
    kind: "exact_match",
    expected: "Paris",
    substring: "",
    caseSensitive: false,
    minimum: "",
    maximum: "",
  });
  const contains = buildDefinitionPayload({
    name: "Approval marker",
    description: "",
    kind: "contains",
    expected: "",
    substring: "APPROVED",
    caseSensitive: true,
    minimum: "",
    maximum: "",
  });
  const numeric = buildDefinitionPayload({
    name: "Confidence band",
    description: "",
    kind: "numeric_threshold",
    expected: "",
    substring: "",
    caseSensitive: true,
    minimum: "0.75",
    maximum: "1",
  });

  assert.deepEqual(exact.payload?.evaluator_config, { expected: "Paris", case_sensitive: false });
  assert.equal(exact.payload?.name, "Final answer");
  assert.deepEqual(contains.payload?.evaluator_config, { substring: "APPROVED", case_sensitive: true });
  assert.deepEqual(numeric.payload?.evaluator_config, { minimum: 0.75, maximum: 1 });
});

test("definition payload builder blocks invalid form combinations", () => {
  const missingName = buildDefinitionPayload({
    name: " ", description: "", kind: "exact_match", expected: "", substring: "",
    caseSensitive: true, minimum: "", maximum: "",
  });
  const badRange = buildDefinitionPayload({
    name: "Range", description: "", kind: "numeric_threshold", expected: "", substring: "",
    caseSensitive: true, minimum: "2", maximum: "1",
  });

  assert.equal(missingName.payload, null);
  assert.deepEqual(missingName.errors, { name: "Name is required.", expected: "Expected text is required." });
  assert.equal(badRange.payload, null);
  assert.equal(badRange.errors.threshold, "Minimum cannot exceed maximum.");
});

test("run status and progress copy is accurate rather than invented", () => {
  assert.equal(formatProgress({ ...baseRun, status: "running", subject_count: 8, result_count: 0 }), "0 / 8 results persisted");
  assert.match(runStatusMessage({ ...baseRun, status: "queued", subject_count: 3 }), /durably queued/i);
  assert.match(runStatusMessage({ ...baseRun, status: "running", subject_count: 3 }), /evaluation running/i);
  assert.match(runStatusMessage({ ...baseRun, status: "failed", error_message: "safe failure" }), /safe failure/i);
  assert.equal(formatScore(null), "Not scored");
  assert.equal(formatScore(0.875), "87.5%");
});

test("polling is limited to non-terminal asynchronous states", () => {
  assert.equal(shouldPollRun("pending"), false);
  assert.equal(shouldPollRun("queued"), true);
  assert.equal(shouldPollRun("running"), true);
  assert.equal(shouldPollRun("completed"), false);
  assert.equal(shouldPollRun("failed"), false);
});

test("result filter state is bounded and generates internal URLs", () => {
  assert.equal(parseOutcomeFilter("passed"), "passed");
  assert.equal(parseOutcomeFilter("unknown"), undefined);
  assert.equal(parseResultOffset("40"), 40);
  assert.equal(parseResultOffset("-20"), 0);
  assert.equal(parseResultOffset("https://evil.example"), 0);
  assert.equal(buildRunResultsHref("run/id", "error", 20), "/evaluations/runs/run%2Fid?outcome=error&offset=20");
  assert.equal(buildRunResultsHref("run/id", undefined, 0), "/evaluations/runs/run%2Fid");
  assert.equal(resultEmptyMessage("failed"), "No failed results match this filter.");
  assert.equal(resultEmptyMessage(undefined), "No evaluation results have been persisted yet.");
});

test("result labels and trace links are explicit and safely encoded", () => {
  assert.equal(outcomeLabel("passed"), "Passed");
  assert.equal(outcomeLabel("failed"), "Failed");
  assert.equal(outcomeLabel("error"), "Evaluation error");
  assert.equal(buildTraceHref("trace/with spaces"), "/traces/trace%2Fwith%20spaces");
});

test("trace selection toggles IDs without duplicates and respects the backend bound", () => {
  assert.deepEqual(toggleTraceSelection([], "trace-a"), ["trace-a"]);
  assert.deepEqual(toggleTraceSelection(["trace-a"], "trace-a"), []);
  assert.deepEqual(toggleTraceSelection(["trace-a"], "trace-b", 1), ["trace-a"]);
});

test("evaluation API parsing rejects enum drift and inconsistent aggregate counts", () => {
  assert.equal(parseEvaluationRun(baseRun).status, "pending");
  assert.throws(() => parseEvaluationRun({ ...baseRun, status: "finished" }), EvaluationApiError);
  assert.throws(
    () => parseEvaluationRun({ ...baseRun, subject_count: 1, result_count: 2 }),
    EvaluationApiError,
  );
});

test("definition and run creation use typed JSON contracts", async (t) => {
  const requests = [];
  t.mock.method(globalThis, "fetch", async (input, init) => {
    requests.push({ input: String(input), body: JSON.parse(String(init?.body)) });
    if (requests.length === 1) {
      return Response.json({
        id: baseRun.definition_id,
        name: "Exact response",
        description: null,
        evaluator_kind: "exact_match",
        evaluator_config: { expected: "Paris", case_sensitive: true },
        is_enabled: true,
        created_at: baseRun.created_at,
        updated_at: baseRun.created_at,
      }, { status: 201 });
    }
    return Response.json(baseRun, { status: 201 });
  });

  const definition = await createEvaluationDefinition({
    name: "Exact response",
    description: null,
    evaluator_kind: "exact_match",
    evaluator_config: { expected: "Paris", case_sensitive: true },
    is_enabled: true,
  });
  const run = await createEvaluationRun(definition.id);

  assert.equal(run.definition_name, "Exact response");
  assert.deepEqual(requests.map((request) => request.body), [
    {
      name: "Exact response",
      description: null,
      evaluator_kind: "exact_match",
      evaluator_config: { expected: "Paris", case_sensitive: true },
      is_enabled: true,
    },
    { definition_id: baseRun.definition_id },
  ]);
});

test("execution submission preserves deferred delivery as accepted state", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({
    run_id: baseRun.id,
    status: "queued",
    subject_count: 2,
    queue_delivery: "deferred",
  }, { status: 202 }));

  const response = await executeEvaluationRun(baseRun.id, ["trace-a", "trace-b"]);
  assert.equal(response.queue_delivery, "deferred");
  assert.equal(response.status, "queued");
});

test("API failures expose safe structured messages including 404", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({
    error: { code: "EVALUATION_RUN_NOT_FOUND", message: "evaluation run was not found" },
  }, { status: 404 }));

  await assert.rejects(
    createEvaluationRun(baseRun.definition_id),
    (error) => error instanceof EvaluationApiError
      && error.status === 404
      && error.code === "EVALUATION_RUN_NOT_FOUND"
      && error.message === "evaluation run was not found",
  );
});
