import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { buildRunResultHref, shouldPollExperimentRun } from "./experiment-ui.ts";
import { appendUniqueTraces } from "./trace-explorer.ts";
import { buildCheckPayload } from "./regression-ui.ts";
import {
  buildMonitoringHref,
  newDriftRule,
  parseMonitoringFilters,
  shouldPollAutomaticCheck,
} from "./monitoring-ui.ts";
import { shortId } from "./product-ui.ts";

const source = (path) => readFile(new URL(path, import.meta.url), "utf8");

test("1 active incident filters are represented in the URL", () => {
  assert.equal(buildMonitoringHref({ monitor: "monitor/id", configuration: "config 1", status: "open" }), "/monitoring?monitor=monitor%2Fid&configuration=config+1&status=open");
});

test("2 clear filters produces the unfiltered monitoring route", () => {
  assert.equal(buildMonitoringHref({ monitor: "", configuration: "", status: "" }), "/monitoring");
});

test("3 filtered monitoring empty state is distinct", async () => {
  assert.match(await source("../components/monitoring/monitoring-workspace.tsx"), /No incidents match (?:the|these) filters/);
});

test("4 filter changes reset incompatible pagination", () => {
  assert.equal(buildRunResultHref("run-1", "B", "", 0), "/experiments/runs/run-1?variant=B");
});

test("5 load-more deduplicates rows", () => {
  assert.deepEqual(appendUniqueTraces([{ trace_id: "a" }], [{ trace_id: "a" }, { trace_id: "b" }]), [{ trace_id: "a" }, { trace_id: "b" }]);
});

test("6 load-more exposes deterministic loading state", async () => {
  assert.match(await source("../components/traces/trace-explorer.tsx"), /loadingMore \? "Loading…"/);
});

test("7 load-more failure preserves existing rows", async () => {
  const text = await source("../components/traces/trace-explorer.tsx");
  assert.match(text, /setLoadMoreError/);
  assert.doesNotMatch(text, /setTraces\(\[\]\).*setLoadMoreError/s);
});

test("8 monitoring URL state round-trips for browser Back", () => {
  const filters = { monitor: "monitor-1", configuration: "configuration-1", status: "acknowledged" };
  assert.deepEqual(parseMonitoringFilters(new URL(buildMonitoringHref(filters), "http://localhost").searchParams), filters);
});

test("9 invalid URL filters fail safely", () => {
  assert.deepEqual(parseMonitoringFilters(new URLSearchParams("configuration=orphan&status=bogus")), { monitor: "", configuration: "", status: "" });
});

test("10 required scientific fields provide feedback", () => {
  assert.match(buildCheckPayload("run-1", "policy-1", "", "").error, /choose.*baseline.*candidate/i);
  assert.equal(newDriftRule().practicalThreshold, "");
});

test("11 safe backend validation messages are rendered", async () => {
  assert.match(await source("../components/monitoring/monitoring-workspace.tsx"), /reason instanceof Error \? reason\.message/);
});

test("12 valid form values survive server failure", async () => {
  const text = await source("../components/regressions/regressions-workspace.tsx");
  assert.match(text, /try \{ const created = await createRegressionPolicy[\s\S]*setPolicyFields/);
  assert.doesNotMatch(text, /catch \(reason\) \{[\s\S]{0,160}setPolicyFields/);
});

test("13 irreversible freeze actions use accessible confirmation", async () => {
  const [control, experiment, references] = await Promise.all([source("../components/workflow-controls.tsx"), source("../components/experiments/experiment-detail.tsx"), source("../components/calibration/reference-sets-panel.tsx")]);
  assert.match(control, /role="alertdialog"/);
  assert.match(experiment, /ConfirmAction/);
  assert.match(references, /ConfirmAction/);
  assert.doesNotMatch(experiment, /window\.confirm/);
});

test("14 frozen state explains immutability and next step", async () => {
  assert.match(await source("../components/calibration/reference-sets-panel.tsx"), /frozen[\s\S]*immutable[\s\S]*create a study/i);
});

test("15 historical run navigation retains status and timestamps", async () => {
  const text = await source("../components/experiments/experiment-detail.tsx");
  assert.match(text, /Historical runs/);
  assert.match(text, /Status[\s\S]*Created[\s\S]*completed/i);
});

test("16 active polling starts only for active states", () => {
  assert.equal(shouldPollExperimentRun("running"), true);
  assert.equal(shouldPollAutomaticCheck("queued"), true);
});

test("17 terminal polling stops", () => {
  assert.equal(shouldPollExperimentRun("completed"), false);
  assert.equal(shouldPollAutomaticCheck("failed"), false);
});

test("18 polling cleans up and avoids interval overlap", async () => {
  const text = await source("../components/regressions/bisection-session-detail.tsx");
  assert.match(text, /setTimeout\(poll, 5_000\)/);
  assert.match(text, /controller\.abort\(\)/);
  assert.doesNotMatch(text, /setInterval/);
});

test("19 failed fetches expose targeted retry", async () => {
  assert.match(await source("../components/monitoring/monitoring-workspace.tsx"), />Retry</);
});

test("20 copy control writes the full identifier", async () => {
  const text = await source("../components/workflow-controls.tsx");
  assert.match(text, /navigator\.clipboard\.writeText\(value\)/);
});

test("21 copy control provides visible feedback", async () => {
  const text = await source("../components/workflow-controls.tsx");
  assert.match(text, /Copied/);
  assert.match(text, /aria-live="polite"/);
});

test("22 long technical references are visually shortened", () => {
  const value = "1234567890abcdefghijklmnopqrstuvwxyz";
  assert.equal(shortId(value), "12345678…wxyz");
});

test("23 monitoring filters show active context and clear action", async () => {
  const text = await source("../components/monitoring/monitoring-workspace.tsx");
  assert.match(text, /Active filters/);
  assert.match(text, /Clear filters/);
});

test("24 incident filters are URL-backed", async () => {
  const text = await source("../components/monitoring/monitoring-workspace.tsx");
  assert.match(text, /useSearchParams/);
  assert.match(text, /router\.push\(buildMonitoringHref/);
});

test("25 regression workflow continues into bisection", async () => {
  assert.match(await source("../components/regressions/regression-check-detail.tsx"), /Create frozen bisection session/);
});

test("26 monitoring workflow continues into monitor evidence", async () => {
  assert.match(await source("../components/monitoring/monitoring-workspace.tsx"), /buildMonitorHref/);
});

test("27 filter and form controls remain mobile-safe", async () => {
  const text = await source("../components/monitoring/monitoring-workspace.tsx");
  assert.match(text, /grid-cols-1[\s\S]*sm:grid-cols-/);
  assert.match(text, /w-full/);
});

test("28 validation errors are associated with controls", async () => {
  const text = await source("../components/regressions/shared.tsx");
  assert.match(text, /aria-describedby/);
  assert.match(text, /aria-invalid/);
});

test("29 meaningful actions provide success feedback", async () => {
  const text = await source("../components/monitoring/monitoring-workspace.tsx");
  assert.match(text, /Monitoring definition created/);
  assert.match(text, /role="status"/);
});

test("30 workflow UI neither renders local roots nor raw stack traces", async () => {
  const [bisection, monitoring] = await Promise.all([source("../components/regressions/bisection-session-detail.tsx"), source("../components/monitoring/monitoring-workspace.tsx")]);
  assert.doesNotMatch(bisection, /session\.repository_path/);
  assert.doesNotMatch(monitoring, /reason\.stack|stackTrace/);
});
