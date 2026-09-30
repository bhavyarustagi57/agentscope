import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (path) => readFile(new URL(path, import.meta.url), "utf8");

test("global keyboard focus and reduced-motion preferences remain visible", async () => {
  const css = await source("../app/globals.css");
  assert.match(css, /:focus-visible/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
});

test("the application shell keeps skip, landmark, current-page, and responsive navigation semantics", async () => {
  const text = await source("../components/app-shell.tsx");
  assert.match(text, /href="#main-content"/);
  assert.match(text, /<nav aria-label="Primary"/);
  assert.match(text, /aria-current=.*"page"/);
  assert.match(text, /id="main-content"[\s\S]*tabIndex=\{-1\}/);
  assert.match(text, /grid-cols-2[\s\S]*sm:grid-cols-4[\s\S]*lg:block/);
});

test("irreversible confirmation supports Escape and returns focus to its trigger", async () => {
  const text = await source("../components/workflow-controls.tsx");
  assert.match(text, /triggerRef/);
  assert.match(text, /key === "Escape"/);
  assert.match(text, /triggerRef\.current\?\.focus\(\)/);
});

test("copy controls expose feedback, use full values, and meet the shared touch target", async () => {
  const text = await source("../components/workflow-controls.tsx");
  assert.match(text, /export function CopyableCommand/);
  assert.match(text, /navigator\.clipboard\.writeText\(value\)/);
  assert.match(text, /aria-live="polite"/);
  assert.doesNotMatch(text, /min-h-9/);
  assert.match(text, /min-h-11/);
});

test("all route and workspace skeletons identify busy loading state", async () => {
  const files = [
    "../app/monitoring/page.tsx",
    "../app/evaluations/runs/[runId]/page.tsx",
    "../app/traces/page.tsx",
    "../app/calibration/runs/[runId]/page.tsx",
    "../app/experiments/page.tsx",
    "../app/regressions/page.tsx",
    "../app/experiments/runs/[runId]/page.tsx",
    "../components/monitoring/monitoring-workspace.tsx",
    "../components/monitoring/monitor-detail.tsx",
    "../components/monitoring/incident-detail.tsx",
    "../components/calibration/calibration-workspace.tsx",
  ];
  for (const file of files) {
    const text = await source(file);
    assert.match(text, /aria-busy="true"/, file);
  }
});

test("every local reusable form field associates hints and errors with its control", async () => {
  const files = [
    "../components/calibration/shared.tsx",
    "../components/evaluations/definition-form.tsx",
    "../components/experiments/experiment-configuration.tsx",
    "../components/monitoring/monitoring-workspace.tsx",
  ];
  for (const file of files) {
    const text = await source(file);
    assert.match(text, /useId/, file);
    assert.match(text, /cloneElement/, file);
    assert.match(text, /aria-describedby/, file);
    assert.match(text, /aria-invalid/, file);
  }
});

test("monitoring preserves field-level errors and contextual rule actions", async () => {
  const text = await source("../components/monitoring/monitoring-workspace.tsx");
  assert.match(text, /monitorErrors/);
  assert.match(text, /policyErrors/);
  assert.match(text, /aria-label=\{`Move rule \$\{index \+ 1\} up`\}/);
  assert.match(text, /aria-label=\{`Move rule \$\{index \+ 1\} down`\}/);
  assert.match(text, /aria-label=\{`Remove rule \$\{index \+ 1\}`\}/);
});

test("monitoring evidence tables provide captions and scoped column headers", async () => {
  const files = [
    "../components/monitoring/monitoring-workspace.tsx",
    "../components/monitoring/monitor-detail.tsx",
    "../components/monitoring/incident-detail.tsx",
  ];
  for (const file of files) {
    const text = await source(file);
    const tableCount = (text.match(/<table/g) ?? []).length;
    assert.equal((text.match(/<caption/g) ?? []).length, tableCount, file);
    assert.match(text, /<th scope="col"/, file);
  }
});

test("shared state panels support a route-level heading", async () => {
  for (const file of ["../components/calibration/shared.tsx", "../components/experiments/shared.tsx"]) {
    const text = await source(file);
    assert.match(text, /headingLevel/, file);
    assert.match(text, /const Heading/, file);
  }
});

test("standalone detail failures render an h1 state heading", async () => {
  const files = [
    "../components/calibration/calibration-run-detail.tsx",
    "../components/experiments/experiment-detail.tsx",
    "../components/experiments/experiment-run-detail.tsx",
    "../components/regressions/regression-check-detail.tsx",
    "../components/regressions/bisection-session-detail.tsx",
    "../components/monitoring/monitor-detail.tsx",
    "../components/monitoring/incident-detail.tsx",
  ];
  for (const file of files) assert.match(await source(file), /headingLevel="h1"/, file);
});

test("the first-run demo command is copyable without losing horizontal overflow safety", async () => {
  const text = await source("../components/overview-dashboard.tsx");
  assert.match(text, /CopyableCommand/);
  assert.match(text, /uv run --project apps\/api python apps\/api\/scripts\/seed_demo\.py/);
  assert.match(await source("../components/workflow-controls.tsx"), /overflow-x-auto/);
});

test("accepted product copy does not expose implementation-phase labels", async () => {
  const files = [
    "../components/experiments/experiment-configuration.tsx",
    "../components/overview-dashboard.tsx",
    "../components/monitoring/monitoring-workspace.tsx",
  ];
  for (const file of files) assert.doesNotMatch(await source(file), /Phase \d/i, file);
});

test("historical experiment identifiers wrap safely on narrow screens", async () => {
  const text = await source("../components/experiments/experiment-detail.tsx");
  assert.match(text, /href=\{`\/experiments\/runs[\s\S]*className="[^"]*break-all/);
});

test("loading, filtered-empty, retry, polling cleanup, and demo provenance remain explicit", async () => {
  const [overview, monitoring, bisection] = await Promise.all([
    source("../components/overview-dashboard.tsx"),
    source("../components/monitoring/monitoring-workspace.tsx"),
    source("../components/regressions/bisection-session-detail.tsx"),
  ]);
  assert.match(overview, /Sample evidence is loaded/);
  assert.match(overview, /Getting started/);
  assert.match(monitoring, /No incidents match these filters/);
  assert.match(monitoring, />Retry</);
  assert.match(bisection, /controller\.abort\(\)/);
  assert.match(bisection, /setTimeout\(poll, 5_000\)/);
  assert.doesNotMatch(bisection, /setInterval/);
});
