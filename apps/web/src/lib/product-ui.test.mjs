import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { NAVIGATION_GROUPS, PRIMARY_NAVIGATION } from "./navigation.ts";
import { humanizeStatus, overviewAvailability, shortId, statusTone } from "./product-ui.ts";
import { hasDemoEvidence, isEmptyOverview, settleOverviewSources, summarizeMonitoringIncidents } from "./product-overview.ts";

const source = (path) => readFile(new URL(path, import.meta.url), "utf8");

test("navigation groups existing routes by operator intent", () => {
  assert.deepEqual(NAVIGATION_GROUPS.map((group) => group.label), ["Workspace", "Observe", "Evaluate", "Investigate"]);
});

test("Observe contains traces and monitoring", () => {
  assert.deepEqual(NAVIGATION_GROUPS.find((group) => group.label === "Observe").items.map((item) => item.label), ["Traces", "Monitoring"]);
});

test("Evaluate contains evaluations calibration and experiments", () => {
  assert.deepEqual(NAVIGATION_GROUPS.find((group) => group.label === "Evaluate").items.map((item) => item.label), ["Evaluations", "Calibration", "Experiments"]);
});

test("Investigate contains regressions", () => {
  assert.deepEqual(NAVIGATION_GROUPS.find((group) => group.label === "Investigate").items.map((item) => item.label), ["Regressions"]);
});

test("primary navigation remains a compatible flattened route list", () => {
  assert.equal(PRIMARY_NAVIGATION.length, 7);
  assert.equal(new Set(PRIMARY_NAVIGATION.map((item) => item.href)).size, 7);
});

test("shell has no phase badge and renders grouped navigation", async () => {
  const text = await source("../components/app-shell.tsx");
  assert.doesNotMatch(text, /PHASE\s*0?8/i);
  assert.match(text, /NAVIGATION_GROUPS/);
});

test("shell retains current-page and skip-link accessibility", async () => {
  const text = await source("../components/app-shell.tsx");
  assert.match(text, /aria-current/);
  assert.match(text, /Skip to content/);
});

test("status language humanizes durable enum tokens", () => assert.equal(humanizeStatus("regression_detected"), "Regression detected"));
test("status language preserves acronyms", () => assert.equal(humanizeStatus("llm_error"), "LLM error"));
test("status tone distinguishes active states", () => assert.equal(statusTone("running"), "active"));
test("status tone distinguishes attention states", () => assert.equal(statusTone("failed"), "attention"));
test("status tone leaves factual terminal states neutral", () => assert.equal(statusTone("completed"), "neutral"));
test("long identifiers abbreviate without mutation", () => assert.equal(shortId("1234567890abcdef"), "12345678…cdef"));
test("short identifiers remain intact", () => assert.equal(shortId("run-1"), "run-1"));

test("overview availability reports exact source coverage", () => {
  assert.deepEqual(overviewAvailability([{ state: "ready" }, { state: "error" }, { state: "empty" }]), { available: 2, total: 3 });
});

test("overview source settlement isolates a partial failure", async () => {
  const result = await settleOverviewSources({ traces: Promise.resolve({ count: 2 }), evaluations: Promise.reject(new Error("offline")) });
  assert.equal(result.traces.state, "ready");
  assert.equal(result.evaluations.state, "error");
  assert.match(result.evaluations.message, /unavailable/i);
});

test("overview source settlement preserves explicit empty evidence", async () => {
  const result = await settleOverviewSources({ traces: Promise.resolve({ count: 0 }) });
  assert.equal(result.traces.state, "empty");
});

test("overview onboarding appears only when every source is successfully empty", () => {
  assert.equal(isEmptyOverview([{ state: "empty", count: 0 }, { state: "empty", count: 0 }]), true);
  assert.equal(isEmptyOverview([{ state: "empty", count: 0 }, { state: "ready", count: 1 }]), false);
  assert.equal(isEmptyOverview([{ state: "empty", count: 0 }, { state: "error", message: "offline" }]), false);
});

test("overview identifies sample evidence without inferring it from arbitrary data", () => {
  assert.equal(hasDemoEvidence([{ state: "ready", count: 1, demo: true }]), true);
  assert.equal(hasDemoEvidence([{ state: "ready", count: 1 }, { state: "empty", count: 0 }]), false);
});

test("overview monitoring is empty when there are no incidents", () => {
  assert.deepEqual(summarizeMonitoringIncidents([]), { count: 0, active: 0, attention: 0 });
});

test("overview monitoring counts an open incident as active evidence", () => {
  assert.deepEqual(summarizeMonitoringIncidents([{ status: "open" }]), { count: 1, active: 1, attention: 1 });
});

test("overview monitoring counts an acknowledged unresolved incident as active evidence", () => {
  assert.deepEqual(summarizeMonitoringIncidents([{ status: "acknowledged" }]), { count: 1, active: 1, attention: 1 });
});

test("overview monitoring excludes resolved-only evidence", () => {
  assert.deepEqual(summarizeMonitoringIncidents([{ status: "resolved" }]), { count: 0, active: 0, attention: 0 });
});

test("overview monitoring counts only open and acknowledged incidents in mixed evidence", () => {
  assert.deepEqual(
    summarizeMonitoringIncidents([{ status: "open" }, { status: "acknowledged" }, { status: "resolved" }]),
    { count: 2, active: 2, attention: 2 },
  );
});

test("overview exposes exact CLI-only demo guidance and valid workflow routes", async () => {
  const text = await source("../components/overview-dashboard.tsx");
  assert.match(text, /uv run --project apps\/api python apps\/api\/scripts\/seed_demo\.py/);
  for (const route of ["/traces", "/evaluations", "/calibration", "/experiments", "/regressions", "/monitoring"]) {
    assert.match(text, new RegExp(`['\"]${route}['\"]`));
  }
  assert.match(text, /Sample evidence|Demo evidence/);
  assert.doesNotMatch(text, /wipe-and-seed|fetch\([^)]*seed/i);
});

test("overview uses six bounded existing product sources", async () => {
  const text = await source("../components/overview-dashboard.tsx");
  for (const call of ["listTraces", "listEvaluationRuns", "listJudgeRuns", "listExperiments", "listRegressionChecks", "listMonitoringIncidents"]) assert.match(text, new RegExp(call));
  assert.match(text, /settleOverviewSources/);
});

test("overview does not poll or invent a health score", async () => {
  const text = await source("../components/overview-dashboard.tsx");
  assert.doesNotMatch(text, /setInterval|setTimeout|health score|trust score/i);
});

test("overview exposes loading error empty and retry states", async () => {
  const text = await source("../components/overview-dashboard.tsx");
  for (const phrase of ["Loading evidence", "Unavailable", "No evidence yet", "Retry unavailable sources"]) assert.match(text, new RegExp(phrase, "i"));
});

test("overview cards link to all product areas", async () => {
  const text = await source("../components/overview-dashboard.tsx");
  for (const href of ["/traces", "/evaluations", "/calibration", "/experiments", "/regressions", "/monitoring"]) assert.match(text, new RegExp(`href: \\"${href}\\"`));
});

test("home page renders the operational overview", async () => assert.match(await source("../app/page.tsx"), /OverviewDashboard/));

test("shared page header and breadcrumbs are semantic and reusable", async () => {
  const text = await source("../components/product-context.tsx");
  assert.match(text, /export function PageHeader/);
  assert.match(text, /export function Breadcrumbs/);
  assert.match(text, /aria-label="Breadcrumb"/);
});

test("regression evidence links back to its experiment run", async () => {
  const text = await source("../components/regressions/regression-check-detail.tsx");
  assert.match(text, /experiments\/runs/);
  assert.match(text, /Experiment run/);
});

test("bisection evidence links back to its regression check", async () => {
  const text = await source("../components/regressions/bisection-session-detail.tsx");
  assert.match(text, /regressions.*regression_check_id/s);
  assert.match(text, /Regression check/);
});

test("incident evidence links back to its monitor", async () => {
  const text = await source("../components/monitoring/incident-detail.tsx");
  assert.match(text, /monitoring_definition_id/);
  assert.match(text, /Monitor evidence/);
});

test("drift comparisons retain monitor context", async () => {
  const text = await source("../components/monitoring/monitor-detail.tsx");
  assert.match(text, /comparison=/);
  assert.match(text, /Monitoring/);
});

test("responsive navigation and overview keep narrow-screen layouts", async () => {
  const texts = await Promise.all([source("../components/app-shell.tsx"), source("../components/overview-dashboard.tsx")]);
  assert.ok(texts.every((text) => /sm:|lg:|xl:/.test(text)));
});

test("existing deep-link route families remain present", async () => {
  const routes = ["../app/traces/[traceId]/page.tsx", "../app/evaluations/runs/[runId]/page.tsx", "../app/experiments/runs/[runId]/page.tsx", "../app/regressions/bisections/[sessionId]/page.tsx", "../app/monitoring/incidents/[incidentId]/page.tsx"];
  const texts = await Promise.all(routes.map(source));
  assert.ok(texts.every((text) => text.includes("AppShell")));
});
