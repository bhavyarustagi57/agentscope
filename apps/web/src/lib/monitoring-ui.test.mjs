import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  automaticCheckLabel,
  buildAutomaticConfigurationPayload,
  buildDriftComparisonPayload,
  buildDriftPolicyPayload,
  buildIncidentHref,
  buildMonitorHref,
  buildMonitoringDefinitionPayload,
  buildTraceEvidenceHref,
  driftClassificationLabel,
  eventTypeLabel,
  formatOptionalMetric,
  incidentStatusLabel,
  moveRule,
  newDriftRule,
  shouldPollAutomaticCheck,
  shouldPollSnapshot,
  sortIncidentEvents,
  thresholdTypeLabel,
  windowSemantics,
} from "./monitoring-ui.ts";
import {
  acknowledgeIncident,
  createMonitoringDefinition,
  parseAutomaticDriftCheck,
  parseDriftComparison,
  parseMonitoringIncident,
  parseMonitoringSnapshot,
  updateMonitoringDefinition,
} from "./monitoring-api.ts";
import { PRIMARY_NAVIGATION } from "./navigation.ts";

test("monitoring is exposed by exactly one primary navigation item", () => {
  assert.equal(PRIMARY_NAVIGATION.filter((item) => item.label === "Monitoring").length, 1);
  assert.ok(PRIMARY_NAVIGATION.some((item) => item.label === "Monitoring" && item.href === "/monitoring"));
});

test("monitor creation trims bounded fields and preserves trace scope", () => {
  const built = buildMonitoringDefinitionPayload({
    name: "  Checkout health  ", description: "  production evidence  ",
    windowDuration: "1h", traceName: " checkout ", traceStatus: "success",
    evaluationDefinitionId: " eval-1 ",
  });
  assert.deepEqual(built.errors, {});
  assert.deepEqual(built.payload, {
    name: "Checkout health", description: "production evidence", is_enabled: true,
    window_duration: "1h", trace_scope: { trace_name: "checkout", trace_status: "success" },
    evaluation_definition_id: "eval-1",
  });
});

test("monitor creation enforces required and backend-bounded values", () => {
  const built = buildMonitoringDefinitionPayload({
    name: "", description: "x".repeat(2_001), windowDuration: "1h",
    traceName: "x".repeat(501), traceStatus: "", evaluationDefinitionId: "",
  });
  assert.match(built.errors.name, /required/i);
  assert.match(built.errors.description, /2,000/i);
  assert.match(built.errors.traceName, /500/i);
});

test("enable and disable use the typed PATCH contract", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (input, init = {}) => {
    requests.push({ url: String(input), method: init.method, body: init.body });
    return Response.json(monitorFixture({ is_enabled: false }));
  };
  try { await updateMonitoringDefinition("monitor/id", false); } finally { globalThis.fetch = originalFetch; }
  assert.deepEqual(requests, [{
    url: "http://localhost:8000/api/v1/monitoring-definitions/monitor%2Fid",
    method: "PATCH", body: JSON.stringify({ is_enabled: false }),
  }]);
});

test("monitoring API preserves sanitized FastAPI detail errors", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => Response.json({ detail: { code: "INVALID_MONITORING_WINDOW", message: "window must be closed" } }, { status: 422 });
  try {
    await assert.rejects(createMonitoringDefinition({}), (error) => error.status === 422 && error.code === "INVALID_MONITORING_WINDOW" && error.message === "window must be closed");
  } finally { globalThis.fetch = originalFetch; }
});

test("closed-window semantics are explicit and half open", () => {
  assert.equal(windowSemantics(), "[start, end) aligned UTC closed windows");
});

test("snapshot parser preserves canonical server aggregates", () => {
  const snapshot = parseMonitoringSnapshot(snapshotFixture());
  assert.equal(snapshot.trace_count, 20);
  assert.equal(snapshot.mean_duration_ms, 125.5);
  assert.equal(snapshot.p95_duration_ms, 250);
  assert.equal(snapshot.total_tokens, 900);
  assert.equal(snapshot.evaluation_pass_rate, 0.8);
});

test("null snapshot metrics render unavailable and never zero", () => {
  assert.equal(formatOptionalMetric(null), "Not available");
  assert.equal(formatOptionalMetric(0), "0");
  assert.equal(parseMonitoringSnapshot(snapshotFixture({ p95_duration_ms: null })).p95_duration_ms, null);
});

test("all snapshot lifecycle values remain distinct", () => {
  for (const status of ["pending", "queued", "running", "completed", "failed"]) {
    assert.equal(parseMonitoringSnapshot(snapshotFixture({ status })).status, status);
  }
});

test("drift policy builder sends supported ordered rule contracts", () => {
  const first = { ...newDriftRule(), practicalThreshold: "0.1" };
  const second = { ...newDriftRule(), metric: "p95_duration_ms", thresholdType: "relative", practicalThreshold: "0.2" };
  const built = buildDriftPolicyPayload({ name: "  Runtime drift  ", description: "  bounded  ", rules: [first, second] });
  assert.deepEqual(built.errors, {});
  assert.equal(built.payload.rules[0].metric, "trace_failure_rate");
  assert.equal(built.payload.rules[1].metric, "p95_duration_ms");
});

test("multiple policy rules can be reordered without mutation", () => {
  const rules = [{ ...newDriftRule(), metric: "trace_count" }, { ...newDriftRule(), metric: "mean_duration_ms" }];
  const moved = moveRule(rules, 1, -1);
  assert.deepEqual(moved.map((rule) => rule.metric), ["mean_duration_ms", "trace_count"]);
  assert.deepEqual(rules.map((rule) => rule.metric), ["trace_count", "mean_duration_ms"]);
});

test("threshold type labels preserve absolute and relative meaning", () => {
  assert.equal(thresholdTypeLabel("absolute"), "Absolute change");
  assert.equal(thresholdTypeLabel("relative"), "Relative to baseline");
});

test("policy builder validates minimum sample fields", () => {
  const rule = { ...newDriftRule(), minimumBaselineSamples: "0", minimumCurrentSamples: "1000001" };
  const built = buildDriftPolicyPayload({ name: "Policy", description: "", rules: [rule] });
  assert.match(built.errors["rules.0.minimumBaselineSamples"], /between/i);
  assert.match(built.errors["rules.0.minimumCurrentSamples"], /between/i);
});

test("manual comparison requires explicit distinct snapshots and policy", () => {
  assert.deepEqual(buildDriftComparisonPayload("policy-1", "baseline-1", "current-1"), {
    payload: { drift_policy_id: "policy-1", baseline_snapshot_id: "baseline-1", current_snapshot_id: "current-1" }, error: null,
  });
  assert.match(buildDriftComparisonPayload("policy-1", "same", "same").error, /differ/i);
});

test("drift findings retain canonical deltas, samples, and classification", () => {
  const comparison = parseDriftComparison(comparisonFixture());
  assert.equal(comparison.findings[0].absolute_delta, 0.2);
  assert.equal(comparison.findings[0].baseline_sample_count, 100);
  assert.equal(comparison.findings[0].classification, "drift_detected");
});

test("insufficient evidence remains distinct from no drift", () => {
  assert.equal(driftClassificationLabel("insufficient_evidence"), "Insufficient evidence");
  assert.notEqual(driftClassificationLabel("insufficient_evidence"), driftClassificationLabel("no_drift_detected"));
});

test("nullable descriptive p values stay unavailable", () => {
  const comparison = parseDriftComparison(comparisonFixture({ p_value: null, z_statistic: null }));
  assert.equal(comparison.findings[0].p_value, null);
  assert.equal(formatOptionalMetric(comparison.findings[0].p_value), "Not available");
});

test("automatic configuration builder sends previous-window settings", () => {
  const built = buildAutomaticConfigurationPayload({
    name: "  Hourly guard  ", monitoringDefinitionId: "monitor-1", driftPolicyId: "policy-1",
    cooldownSeconds: "3600", resolveAfterCleanWindows: "2",
  });
  assert.deepEqual(built.errors, {});
  assert.deepEqual(built.payload, {
    name: "Hourly guard", monitoring_definition_id: "monitor-1", drift_policy_id: "policy-1",
    is_enabled: true, baseline_strategy: "previous_window", cooldown_seconds: 3600,
    resolve_after_clean_windows: 2,
  });
});

test("automatic configuration validates cooldown and clean-window bounds", () => {
  const built = buildAutomaticConfigurationPayload({
    name: "Guard", monitoringDefinitionId: "monitor-1", driftPolicyId: "policy-1",
    cooldownSeconds: "604801", resolveAfterCleanWindows: "21",
  });
  assert.match(built.errors.cooldownSeconds, /between/i);
  assert.match(built.errors.resolveAfterCleanWindows, /between/i);
});

test("previous-window language means immediately adjacent completed evidence", async () => {
  const source = await readFile(new URL("../components/monitoring/monitor-detail.tsx", import.meta.url), "utf8");
  assert.match(source, /immediately preceding adjacent completed window/i);
  assert.match(source, /missing baseline.*skipped/is);
});

test("automatic check skipped state remains a non-decision", () => {
  assert.equal(automaticCheckLabel(parseAutomaticDriftCheck(checkFixture({ status: "skipped", skip_reason: "baseline_snapshot_missing" }))), "Skipped — baseline snapshot missing");
});

test("automatic check infrastructure failure remains distinct", () => {
  assert.equal(automaticCheckLabel(parseAutomaticDriftCheck(checkFixture({ status: "failed", failure_reason: "comparison_failure" }))), "Failed — comparison failure");
});

test("completed automatic check links to canonical drift evidence", () => {
  assert.equal(automaticCheckLabel(parseAutomaticDriftCheck(checkFixture())), "Completed — view comparison");
});

test("drift classification uses the canonical three-way language", () => {
  assert.equal(driftClassificationLabel("drift_detected"), "Drift detected");
  assert.equal(driftClassificationLabel("no_drift_detected"), "No drift detected");
});

test("incident OPEN rendering remains explicit", () => assert.equal(incidentStatusLabel("open"), "Open"));
test("incident ACKNOWLEDGED rendering remains explicit", () => assert.equal(incidentStatusLabel("acknowledged"), "Acknowledged"));
test("incident RESOLVED rendering remains explicit", () => assert.equal(incidentStatusLabel("resolved"), "Resolved"));

test("acknowledge action uses the dedicated endpoint", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (input, init = {}) => {
    requests.push({ url: String(input), method: init.method });
    return Response.json(incidentFixture({ status: "acknowledged", acknowledged_at: "2026-09-25T02:00:00Z" }));
  };
  try { await acknowledgeIncident("incident/id"); } finally { globalThis.fetch = originalFetch; }
  assert.deepEqual(requests, [{ url: "http://localhost:8000/api/v1/monitoring-incidents/incident%2Fid/acknowledge", method: "POST" }]);
});

test("incident UI states acknowledgement is not resolution", async () => {
  const source = await readFile(new URL("../components/monitoring/incident-detail.tsx", import.meta.url), "utf8");
  assert.match(source, /Acknowledgement means the incident has been seen/);
  assert.match(source, /does not mean the drift is resolved/);
});

test("incident parser preserves occurrence and clean-window counts", () => {
  const incident = parseMonitoringIncident(incidentFixture());
  assert.equal(incident.occurrence_count, 3);
  assert.equal(incident.consecutive_clean_count, 1);
});

test("incident events are sorted chronologically without fabrication", () => {
  const events = [eventFixture("incident_resolved", "2026-09-25T04:00:00Z"), eventFixture("incident_opened", "2026-09-25T01:00:00Z")];
  assert.deepEqual(sortIncidentEvents(events).map((event) => event.event_type), ["incident_opened", "incident_resolved"]);
  assert.equal(eventTypeLabel("drift_reoccurred"), "Drift reoccurred");
});

test("snapshot polling is active only", () => {
  for (const status of ["pending", "queued", "running"]) assert.equal(shouldPollSnapshot(status), true);
  for (const status of ["completed", "failed"]) assert.equal(shouldPollSnapshot(status), false);
});

test("automatic-check polling stops at every terminal state", () => {
  for (const status of ["pending", "queued", "running"]) assert.equal(shouldPollAutomaticCheck(status), true);
  for (const status of ["completed", "failed", "skipped"]) assert.equal(shouldPollAutomaticCheck(status), false);
});

test("internal evidence links encode identifiers and supported trace filters", () => {
  assert.equal(buildMonitorHref("monitor/id"), "/monitoring/monitor%2Fid");
  assert.equal(buildIncidentHref("incident/id"), "/monitoring/incidents/incident%2Fid");
  assert.equal(buildTraceEvidenceHref({ trace_name: "checkout / agent", trace_status: "error" }), "/traces?name=checkout+%2F+agent&status=error");
});

test("monitoring layouts include responsive and overflow-safe evidence patterns", async () => {
  const sources = await Promise.all([
    readFile(new URL("../components/monitoring/monitoring-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/monitoring/monitor-detail.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/monitoring/incident-detail.tsx", import.meta.url), "utf8"),
  ]);
  assert.ok(sources.every((source) => /sm:|lg:|xl:/.test(source)));
  assert.ok(sources.every((source) => /overflow-x-auto|break-all|break-words/.test(source)));
});

function monitorFixture(overrides = {}) {
  return { id: "monitor-1", name: "Checkout", description: null, is_enabled: true, window_duration: "1h", trace_scope: { trace_name: "checkout", trace_status: null }, evaluation_definition_id: null, created_at: "2026-09-25T00:00:00Z", updated_at: "2026-09-25T00:00:00Z", ...overrides };
}

function snapshotFixture(overrides = {}) {
  return { id: "snapshot-1", monitoring_definition_id: "monitor-1", status: "completed", window_start: "2026-09-25T00:00:00Z", window_end: "2026-09-25T01:00:00Z", trace_count: 20, successful_trace_count: 16, failed_trace_count: 4, success_rate: 0.8, failure_rate: 0.2, duration_sample_count: 20, mean_duration_ms: 125.5, median_duration_ms: 100, p95_duration_ms: 250, token_sample_count: 18, input_tokens: 500, output_tokens: 400, total_tokens: 900, mean_total_tokens: 50, evaluated_result_count: 20, passed_evaluation_count: 16, failed_evaluation_count: 3, evaluator_error_count: 1, valid_binary_evaluation_count: 19, evaluation_pass_rate: 0.8, evaluation_error_rate: 0.05, attempt_count: 1, error_message: null, created_at: "2026-09-25T01:00:00Z", queued_at: "2026-09-25T01:00:00Z", started_at: "2026-09-25T01:00:01Z", completed_at: "2026-09-25T01:00:02Z", ...overrides };
}

function findingFixture(overrides = {}) {
  return { rule_position: 0, metric: "trace_failure_rate", direction: "increase", threshold_type: "absolute", practical_threshold: 0.1, minimum_baseline_samples: 10, minimum_current_samples: 10, baseline_sample_count: 100, current_sample_count: 100, baseline_value: 0.1, current_value: 0.3, absolute_delta: 0.2, relative_delta: 2, classification: "drift_detected", z_statistic: 3.2, p_value: 0.001, ...overrides };
}

function comparisonFixture(findingOverrides = {}) {
  return { id: "comparison-1", monitoring_definition_id: "monitor-1", drift_policy_id: "policy-1", baseline_snapshot_id: "snapshot-0", current_snapshot_id: "snapshot-1", classification: "drift_detected", policy_name: "Failure guard", policy_description: null, created_at: "2026-09-25T01:00:03Z", findings: [findingFixture(findingOverrides)] };
}

function checkFixture(overrides = {}) {
  return { id: "check-1", automatic_drift_configuration_id: "configuration-1", baseline_snapshot_id: "snapshot-0", current_snapshot_id: "snapshot-1", drift_comparison_id: "comparison-1", status: "completed", skip_reason: null, failure_reason: null, error_message: null, attempt_count: 1, event_suppressed: false, event_suppression_reason: null, created_at: "2026-09-25T01:00:00Z", queued_at: "2026-09-25T01:00:00Z", started_at: "2026-09-25T01:00:01Z", completed_at: "2026-09-25T01:00:03Z", ...overrides };
}

function incidentFixture(overrides = {}) {
  return { id: "incident-1", automatic_drift_configuration_id: "configuration-1", monitoring_definition_id: "monitor-1", drift_policy_id: "policy-1", status: "open", opened_at: "2026-09-25T01:00:03Z", acknowledged_at: null, resolved_at: null, first_drift_comparison_id: "comparison-1", latest_drift_comparison_id: "comparison-3", resolving_comparison_id: null, latest_classification: "drift_detected", occurrence_count: 3, consecutive_clean_count: 1, created_at: "2026-09-25T01:00:03Z", updated_at: "2026-09-25T03:00:03Z", ...overrides };
}

function eventFixture(eventType, createdAt) {
  return { id: `${eventType}-id`, incident_id: "incident-1", automatic_drift_check_id: "check-1", drift_comparison_id: "comparison-1", event_type: eventType, created_at: createdAt };
}
