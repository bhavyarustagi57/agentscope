import type {
  AutomaticDriftCheck,
  AutomaticDriftCheckStatus,
  DriftClassification,
  DriftPolicyRuleCreate,
  DriftThresholdType,
  MonitoringIncidentEvent,
  MonitoringIncidentStatus,
  MonitoringSnapshotStatus,
  MonitoringTraceScope,
  TraceStatus,
  WindowDuration,
} from "./monitoring-api";

export type MonitoringDefinitionFields = {
  name: string;
  description: string;
  windowDuration: WindowDuration;
  traceName: string;
  traceStatus: TraceStatus | "";
  evaluationDefinitionId: string;
};

export type DriftRuleFields = {
  metric: DriftPolicyRuleCreate["metric"];
  direction: DriftPolicyRuleCreate["direction"];
  thresholdType: DriftThresholdType;
  practicalThreshold: string;
  minimumBaselineSamples: string;
  minimumCurrentSamples: string;
};

export type MonitoringFilters = {
  monitor: string;
  configuration: string;
  status: MonitoringIncidentStatus | "";
};

export function buildMonitoringDefinitionPayload(fields: MonitoringDefinitionFields) {
  const errors: Record<string, string> = {};
  const name = fields.name.trim();
  const description = fields.description.trim();
  const traceName = fields.traceName.trim();
  const evaluationDefinitionId = fields.evaluationDefinitionId.trim();
  if (!name) errors.name = "Monitor name is required.";
  else if (name.length > 200) errors.name = "Monitor name must be 200 characters or fewer.";
  if (description.length > 2_000) errors.description = "Description must be 2,000 characters or fewer.";
  if (traceName.length > 500) errors.traceName = "Trace name must be 500 characters or fewer.";
  return {
    errors,
    payload: {
      name,
      description: description || null,
      is_enabled: true,
      window_duration: fields.windowDuration,
      trace_scope: { trace_name: traceName || null, trace_status: fields.traceStatus || null },
      evaluation_definition_id: evaluationDefinitionId || null,
    },
  };
}

export function newDriftRule(): DriftRuleFields {
  return {
    metric: "trace_failure_rate",
    direction: "increase",
    thresholdType: "absolute",
    practicalThreshold: "",
    minimumBaselineSamples: "10",
    minimumCurrentSamples: "10",
  };
}

export function parseMonitoringFilters(params: URLSearchParams): MonitoringFilters {
  const monitor = params.get("monitor")?.trim() ?? "";
  const statusValue = params.get("status") ?? "";
  const status = (["open", "acknowledged", "resolved"] as const).includes(statusValue as MonitoringIncidentStatus)
    ? statusValue as MonitoringIncidentStatus
    : "";
  return { monitor, configuration: monitor ? params.get("configuration")?.trim() ?? "" : "", status };
}

export function buildMonitoringHref(filters: MonitoringFilters): string {
  const params = new URLSearchParams();
  if (filters.monitor) params.set("monitor", filters.monitor);
  if (filters.monitor && filters.configuration) params.set("configuration", filters.configuration);
  if (filters.status) params.set("status", filters.status);
  return `/monitoring${params.size ? `?${params}` : ""}`;
}

export function moveRule(rules: DriftRuleFields[], index: number, direction: -1 | 1) {
  const target = index + direction;
  if (target < 0 || target >= rules.length) return [...rules];
  const result = [...rules];
  [result[index], result[target]] = [result[target], result[index]];
  return result;
}

export function buildDriftPolicyPayload(fields: {
  name: string;
  description: string;
  rules: DriftRuleFields[];
}) {
  const errors: Record<string, string> = {};
  const name = fields.name.trim();
  const description = fields.description.trim();
  if (!name) errors.name = "Policy name is required.";
  else if (name.length > 200) errors.name = "Policy name must be 200 characters or fewer.";
  if (description.length > 2_000) errors.description = "Description must be 2,000 characters or fewer.";
  if (fields.rules.length < 1 || fields.rules.length > 20) errors.rules = "Provide between 1 and 20 rules.";
  const rules = fields.rules.map((rule, index) => {
    const threshold = Number(rule.practicalThreshold);
    const baseline = Number(rule.minimumBaselineSamples);
    const current = Number(rule.minimumCurrentSamples);
    const prefix = `rules.${index}.`;
    const maximum = rule.thresholdType === "relative" ? 1_000 : rateMetric(rule.metric) ? 1 : 1_000_000_000;
    if (!Number.isFinite(threshold) || threshold <= 0 || threshold > maximum) {
      errors[`${prefix}practicalThreshold`] = `Threshold must be greater than 0 and at most ${maximum}.`;
    }
    if (!boundedInteger(baseline, 1, 1_000_000)) {
      errors[`${prefix}minimumBaselineSamples`] = "Minimum baseline samples must be between 1 and 1,000,000.";
    }
    if (!boundedInteger(current, 1, 1_000_000)) {
      errors[`${prefix}minimumCurrentSamples`] = "Minimum current samples must be between 1 and 1,000,000.";
    }
    return {
      metric: rule.metric,
      direction: rule.direction,
      threshold_type: rule.thresholdType,
      practical_threshold: threshold,
      minimum_baseline_samples: baseline,
      minimum_current_samples: current,
    };
  });
  return { errors, payload: { name, description: description || null, rules } };
}

export function buildDriftComparisonPayload(policyId: string, baselineId: string, currentId: string) {
  const policy = policyId.trim();
  const baseline = baselineId.trim();
  const current = currentId.trim();
  if (!policy || !baseline || !current) return { payload: null, error: "Choose a policy, baseline snapshot, and current snapshot." };
  if (baseline === current) return { payload: null, error: "Baseline and current snapshots must differ." };
  return { payload: { drift_policy_id: policy, baseline_snapshot_id: baseline, current_snapshot_id: current }, error: null };
}

export function buildAutomaticConfigurationPayload(fields: {
  name: string;
  monitoringDefinitionId: string;
  driftPolicyId: string;
  cooldownSeconds: string;
  resolveAfterCleanWindows: string;
}) {
  const errors: Record<string, string> = {};
  const name = fields.name.trim();
  const monitor = fields.monitoringDefinitionId.trim();
  const policy = fields.driftPolicyId.trim();
  const cooldown = Number(fields.cooldownSeconds);
  const cleanWindows = Number(fields.resolveAfterCleanWindows);
  if (!name) errors.name = "Configuration name is required.";
  else if (name.length > 200) errors.name = "Configuration name must be 200 characters or fewer.";
  if (!monitor) errors.monitoringDefinitionId = "Choose a monitor.";
  if (!policy) errors.driftPolicyId = "Choose a drift policy.";
  if (!boundedInteger(cooldown, 0, 604_800)) errors.cooldownSeconds = "Cooldown must be between 0 and 604,800 seconds.";
  if (!boundedInteger(cleanWindows, 1, 20)) errors.resolveAfterCleanWindows = "Clean windows must be between 1 and 20.";
  return {
    errors,
    payload: {
      name,
      monitoring_definition_id: monitor,
      drift_policy_id: policy,
      is_enabled: true,
      baseline_strategy: "previous_window" as const,
      cooldown_seconds: cooldown,
      resolve_after_clean_windows: cleanWindows,
    },
  };
}

export function windowSemantics() { return "[start, end) aligned UTC closed windows"; }
export function formatOptionalMetric(value: number | null, maximumFractionDigits = 3) {
  return value === null ? "Not available" : new Intl.NumberFormat("en-US", { maximumFractionDigits }).format(value);
}
export function formatRate(value: number | null) {
  return value === null ? "Not available" : `${(value * 100).toFixed(1)}%`;
}
export function thresholdTypeLabel(value: DriftThresholdType) {
  return value === "absolute" ? "Absolute change" : "Relative to baseline";
}
export function driftClassificationLabel(value: DriftClassification) {
  return { drift_detected: "Drift detected", no_drift_detected: "No drift detected", insufficient_evidence: "Insufficient evidence" }[value];
}
export function incidentStatusLabel(value: MonitoringIncidentStatus) {
  return { open: "Open", acknowledged: "Acknowledged", resolved: "Resolved" }[value];
}
export function eventTypeLabel(value: MonitoringIncidentEvent["event_type"]) {
  return { incident_opened: "Incident opened", drift_reoccurred: "Drift reoccurred", incident_acknowledged: "Incident acknowledged", incident_resolved: "Incident resolved" }[value];
}
export function automaticCheckLabel(check: AutomaticDriftCheck) {
  if (check.status === "skipped") return `Skipped — ${reasonLabel(check.skip_reason)}`;
  if (check.status === "failed") return `Failed — ${reasonLabel(check.failure_reason)}`;
  if (check.status === "completed") return check.drift_comparison_id ? "Completed — view comparison" : "Completed";
  return check.status.replace(/^./, (letter) => letter.toUpperCase());
}
export function sortIncidentEvents(events: MonitoringIncidentEvent[]) {
  return [...events].sort((left, right) => left.created_at.localeCompare(right.created_at));
}
export function shouldPollSnapshot(status: MonitoringSnapshotStatus) {
  return status === "pending" || status === "queued" || status === "running";
}
export function shouldPollAutomaticCheck(status: AutomaticDriftCheckStatus) {
  return status === "pending" || status === "queued" || status === "running";
}
export function buildMonitorHref(id: string) { return `/monitoring/${encodeURIComponent(id)}`; }
export function buildIncidentHref(id: string) { return `/monitoring/incidents/${encodeURIComponent(id)}`; }
export function buildTraceEvidenceHref(scope: MonitoringTraceScope) {
  const params = new URLSearchParams();
  if (scope.trace_name) params.set("name", scope.trace_name);
  if (scope.trace_status) params.set("status", scope.trace_status);
  const query = params.toString();
  return `/traces${query ? `?${query}` : ""}`;
}

function reasonLabel(value: string | null) {
  return value ? value.replaceAll("_", " ") : "reason unavailable";
}
function boundedInteger(value: number, minimum: number, maximum: number) {
  return Number.isInteger(value) && value >= minimum && value <= maximum;
}
function rateMetric(metric: DriftPolicyRuleCreate["metric"]) {
  return ["trace_failure_rate", "trace_success_rate", "evaluation_pass_rate", "evaluation_error_rate"].includes(metric);
}
