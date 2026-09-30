"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Breadcrumbs } from "@/components/product-context";

import {
  createAutomaticConfiguration,
  createDriftComparison,
  getDriftComparison,
  getMonitoringDefinition,
  listAutomaticChecks,
  listAutomaticConfigurations,
  listDriftComparisons,
  listDriftPolicies,
  listMonitoringIncidents,
  listMonitoringSnapshots,
  materializeMonitoringSnapshot,
  updateAutomaticConfiguration,
  type AutomaticDriftCheck,
  type AutomaticDriftConfiguration,
  type DriftComparison,
  type DriftComparisonSummary,
  type DriftPolicy,
  type MonitoringDefinition,
  type MonitoringIncident,
  type MonitoringSnapshot,
} from "@/lib/monitoring-api";
import {
  automaticCheckLabel,
  buildAutomaticConfigurationPayload,
  buildDriftComparisonPayload,
  buildIncidentHref,
  buildTraceEvidenceHref,
  driftClassificationLabel,
  formatOptionalMetric,
  formatRate,
  incidentStatusLabel,
  shouldPollAutomaticCheck,
  shouldPollSnapshot,
} from "@/lib/monitoring-ui";
import { formatTimestamp } from "@/lib/trace-explorer";
import { Metric, ViewState, buttonClass, inputClass, primaryButtonClass } from "@/components/experiments/shared";

type Evidence = {
  definition: MonitoringDefinition; snapshots: MonitoringSnapshot[]; policies: DriftPolicy[];
  comparisons: DriftComparisonSummary[]; configurations: AutomaticDriftConfiguration[];
  checks: AutomaticDriftCheck[]; incidents: MonitoringIncident[];
};

export function MonitorDetail({ monitorId }: { monitorId: string }) {
  const search = useSearchParams();
  const [evidence, setEvidence] = useState<Evidence | null>(null); const [selected, setSelected] = useState<DriftComparison | null>(null);
  const [loading, setLoading] = useState(true); const [error, setError] = useState<string | null>(null); const [notice, setNotice] = useState<string | null>(null); const [refresh, setRefresh] = useState(0);
  const [windowFields, setWindowFields] = useState({ start: "", end: "" }); const [comparisonFields, setComparisonFields] = useState({ policy: "", baseline: "", current: "" });
  const [configurationFields, setConfigurationFields] = useState({ name: "", policy: "", cooldown: "3600", cleanWindows: "2" });

  const load = useCallback(async (signal?: AbortSignal) => {
    const [definition, snapshots, policies, comparisons, configurations, incidents] = await Promise.all([
      getMonitoringDefinition(monitorId, signal), listMonitoringSnapshots(monitorId, signal), listDriftPolicies(signal),
      listDriftComparisons(monitorId, signal), listAutomaticConfigurations(monitorId, signal), listMonitoringIncidents({ monitorId, signal }),
    ]);
    const checkPages = await Promise.all(configurations.items.map((item) => listAutomaticChecks(item.id, signal)));
    return { definition, snapshots: snapshots.items, policies: policies.items, comparisons: comparisons.items, configurations: configurations.items, checks: checkPages.flatMap((page) => page.items), incidents: incidents.items };
  }, [monitorId]);

  useEffect(() => { const controller = new AbortController(); load(controller.signal).then((value) => { setEvidence(value); setError(null); }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(message(reason)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [load, refresh]);
  useEffect(() => { const comparisonId = search.get("comparison"); if (!comparisonId) return; const controller = new AbortController(); getDriftComparison(comparisonId, controller.signal).then(setSelected).catch((reason: unknown) => { if (!controller.signal.aborted) setNotice(message(reason)); }); return () => controller.abort(); }, [search]);
  useEffect(() => { if (!evidence || !(evidence.snapshots.some((item) => shouldPollSnapshot(item.status)) || evidence.checks.some((item) => shouldPollAutomaticCheck(item.status)))) return; const timer = window.setInterval(() => setRefresh((value) => value + 1), 5_000); return () => window.clearInterval(timer); }, [evidence]);

  if (loading && !evidence) return <div aria-busy="true" role="status" aria-label="Loading monitor" className="h-[34rem] animate-pulse border bg-surface" />;
  if (error && !evidence) return <ViewState title="Couldn’t load monitor" detail={error} role="alert" headingLevel="h1"><Link href="/monitoring" className={`mt-4 ${buttonClass}`}>Back to monitoring</Link></ViewState>;
  if (!evidence) return null;
  const completed = evidence.snapshots.filter((item) => item.status === "completed");

  async function materialize(event: React.FormEvent) { event.preventDefault(); setNotice(null); if (Boolean(windowFields.start) !== Boolean(windowFields.end)) { setNotice("Provide both window boundaries, or leave both blank for the latest closed window."); return; } try { const explicit = windowFields.start && windowFields.end ? { window_start: new Date(windowFields.start).toISOString(), window_end: new Date(windowFields.end).toISOString() } : undefined; const result = await materializeMonitoringSnapshot(monitorId, explicit); setNotice(result.created ? "Snapshot queued." : "That canonical snapshot already exists."); setRefresh((value) => value + 1); } catch (reason) { setNotice(message(reason)); } }
  async function compare(event: React.FormEvent) { event.preventDefault(); const built = buildDriftComparisonPayload(comparisonFields.policy, comparisonFields.baseline, comparisonFields.current); if (!built.payload) { setNotice(built.error); return; } try { const result = await createDriftComparison(built.payload); setSelected(result); setNotice("Comparison completed with canonical backend findings."); setRefresh((value) => value + 1); } catch (reason) { setNotice(message(reason)); } }
  async function configure(event: React.FormEvent) { event.preventDefault(); const built = buildAutomaticConfigurationPayload({ name: configurationFields.name, monitoringDefinitionId: monitorId, driftPolicyId: configurationFields.policy, cooldownSeconds: configurationFields.cooldown, resolveAfterCleanWindows: configurationFields.cleanWindows }); if (Object.keys(built.errors).length) { setNotice(Object.values(built.errors)[0]); return; } try { await createAutomaticConfiguration(built.payload); setNotice("Automatic drift configuration created."); setRefresh((value) => value + 1); } catch (reason) { setNotice(message(reason)); } }

  return <div className="mx-auto max-w-[96rem] break-words">
    <Breadcrumbs items={[{ label: "Monitoring", href: "/monitoring" }, { label: evidence.definition.name }]} />
    <header className="border-b pb-6"><div className="flex flex-wrap items-start justify-between gap-4"><div><p className="font-mono text-xs font-semibold tracking-[0.16em] text-accent">MONITOR DEFINITION</p><h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">{evidence.definition.name}</h1><p className="mt-2 max-w-3xl text-sm leading-6 text-muted">{evidence.definition.description || "No description"}</p></div><span className="border px-3 py-2 text-sm font-semibold">{evidence.definition.is_enabled ? "Enabled" : "Disabled"}</span></div>
      <div className="mt-4 flex flex-wrap gap-3 text-xs text-muted"><span>{evidence.definition.window_duration} windows</span><span>·</span><Link href={buildTraceEvidenceHref(evidence.definition.trace_scope)} className="text-accent hover:underline">Open matching traces</Link>{evidence.definition.evaluation_definition_id && <><span>·</span><Link href={`/evaluations?definition_id=${encodeURIComponent(evidence.definition.evaluation_definition_id)}`} className="text-accent hover:underline">Open evaluation definition</Link></>}</div>
    </header>
    {notice && <p role="status" className="mt-5 border bg-surface p-3 text-sm">{notice}</p>}
    <section className="mt-7 grid gap-5 lg:grid-cols-[22rem_minmax(0,1fr)]"><form onSubmit={materialize} className="border bg-surface p-5"><h2 className="font-semibold">Materialize snapshot</h2><p className="mt-1 text-sm leading-6 text-muted">Leave both dates blank for the latest aligned closed window. Explicit windows are UTC and half-open: <span className="font-mono">[start, end)</span>.</p><Field label="Window start"><input type="datetime-local" value={windowFields.start} onChange={(event) => setWindowFields({ ...windowFields, start: event.target.value })} className={inputClass} /></Field><Field label="Window end"><input type="datetime-local" value={windowFields.end} onChange={(event) => setWindowFields({ ...windowFields, end: event.target.value })} className={inputClass} /></Field><button className={`mt-5 w-full ${primaryButtonClass}`}>Materialize</button></form>
      <div className="min-w-0"><h2 className="font-semibold">Canonical snapshots</h2><p className="mt-1 text-sm text-muted">Pending work refreshes every five seconds; terminal evidence never polls.</p>{evidence.snapshots.length === 0 ? <div className="mt-4"><ViewState title="No snapshots" detail="Materialize the latest closed window to create evidence." /></div> : <div className="mt-4 space-y-4">{evidence.snapshots.map((snapshot) => <article key={snapshot.id} className="border bg-surface p-4"><div className="flex flex-wrap justify-between gap-2"><div><p className="font-mono text-xs text-muted">{formatTimestamp(snapshot.window_start)} → {formatTimestamp(snapshot.window_end)}</p><p className="mt-1 break-all font-mono text-xs text-muted">{snapshot.id}</p></div><span className="font-semibold uppercase">{snapshot.status}</span></div>{snapshot.error_message && <p className="mt-3 text-sm text-red-800">{snapshot.error_message}</p>}<div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6"><Metric label="Traces" value={snapshot.trace_count} detail={`${snapshot.successful_trace_count} successful · ${snapshot.failed_trace_count} failed`} /><Metric label="Success" value={formatRate(snapshot.success_rate)} /><Metric label="Failure" value={formatRate(snapshot.failure_rate)} /><Metric label="Duration samples" value={snapshot.duration_sample_count} /><Metric label="Mean duration" value={formatOptionalMetric(snapshot.mean_duration_ms)} /><Metric label="Median duration" value={formatOptionalMetric(snapshot.median_duration_ms)} /><Metric label="P95 duration" value={formatOptionalMetric(snapshot.p95_duration_ms)} /><Metric label="Token samples" value={snapshot.token_sample_count} /><Metric label="Input tokens" value={formatOptionalMetric(snapshot.input_tokens)} /><Metric label="Output tokens" value={formatOptionalMetric(snapshot.output_tokens)} /><Metric label="Total tokens" value={formatOptionalMetric(snapshot.total_tokens)} /><Metric label="Mean tokens" value={formatOptionalMetric(snapshot.mean_total_tokens)} /><Metric label="Evaluated" value={snapshot.evaluated_result_count} detail={`${snapshot.passed_evaluation_count} passed · ${snapshot.failed_evaluation_count} failed · ${snapshot.evaluator_error_count} errors`} /><Metric label="Eval pass" value={formatRate(snapshot.evaluation_pass_rate)} /><Metric label="Eval error" value={formatRate(snapshot.evaluation_error_rate)} /></div></article>)}</div>}</div>
    </section>
    <section className="mt-10 grid gap-5 xl:grid-cols-[22rem_minmax(0,1fr)]"><form onSubmit={compare} className="border bg-surface p-5"><h2 className="font-semibold">Manual comparison</h2><p className="mt-1 text-sm leading-6 text-muted">Choose two distinct completed snapshots. The backend owns all classifications and statistics.</p><Field label="Policy"><select required value={comparisonFields.policy} onChange={(event) => setComparisonFields({ ...comparisonFields, policy: event.target.value })} className={inputClass}><option value="">Choose policy</option>{evidence.policies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field><SnapshotSelect label="Baseline" value={comparisonFields.baseline} items={completed} onChange={(baseline) => setComparisonFields({ ...comparisonFields, baseline })} /><SnapshotSelect label="Current" value={comparisonFields.current} items={completed} onChange={(current) => setComparisonFields({ ...comparisonFields, current })} /><button className={`mt-5 w-full ${primaryButtonClass}`}>Compare snapshots</button></form>
      <div className="min-w-0"><h2 className="font-semibold">Drift comparisons</h2><p className="mt-1 text-sm leading-6 text-muted">Absolute thresholds use raw change; relative thresholds use change divided by the baseline. Equality counts. Minimum samples gate every rule, and rate p-values are descriptive—not the definition of drift. Insufficient evidence remains distinct from no drift.</p>{selected && <ComparisonFindings comparison={selected} />}<div className="mt-4 overflow-x-auto"><table className="min-w-full text-left text-sm"><caption className="sr-only">Drift comparisons</caption><thead><tr className="border-b"><th scope="col" className="p-3">Classification</th><th scope="col" className="p-3">Policy</th><th scope="col" className="p-3">Created</th><th scope="col" className="p-3">Evidence IDs</th></tr></thead><tbody>{evidence.comparisons.map((item) => <tr key={item.id} className="border-b bg-surface"><td className="p-3 font-semibold"><Link href={`?comparison=${encodeURIComponent(item.id)}`} className="text-accent hover:underline">{driftClassificationLabel(item.classification)}</Link></td><td className="p-3">{item.policy_name}</td><td className="p-3">{formatTimestamp(item.created_at)}</td><td className="break-all p-3 font-mono text-xs">{item.baseline_snapshot_id} → {item.current_snapshot_id}</td></tr>)}</tbody></table></div><h3 className="mt-6 font-semibold">Available policies</h3><ul className="mt-3 grid gap-3 sm:grid-cols-2">{evidence.policies.map((policy) => <li key={policy.id} className="border bg-surface p-4"><p className="font-semibold">{policy.name}</p><ol className="mt-2 space-y-1 text-xs text-muted">{policy.rules.map((rule) => <li key={rule.position}>{rule.position + 1}. {rule.metric} · {rule.direction} · {rule.threshold_type} ≥ {rule.practical_threshold} · samples {rule.minimum_baseline_samples}/{rule.minimum_current_samples}</li>)}</ol></li>)}</ul></div>
    </section>
    <section className="mt-10 grid gap-5 xl:grid-cols-[22rem_minmax(0,1fr)]"><form onSubmit={configure} className="border bg-surface p-5"><h2 className="font-semibold">Automatic checks</h2><p className="mt-1 text-sm leading-6 text-muted">Previous-window means the immediately preceding adjacent completed window. A missing baseline is skipped, never treated as no drift.</p><Field label="Name"><input required maxLength={200} value={configurationFields.name} onChange={(event) => setConfigurationFields({ ...configurationFields, name: event.target.value })} className={inputClass} /></Field><Field label="Policy"><select required value={configurationFields.policy} onChange={(event) => setConfigurationFields({ ...configurationFields, policy: event.target.value })} className={inputClass}><option value="">Choose policy</option>{evidence.policies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field><Field label="Cooldown seconds"><input type="number" min="0" max="604800" value={configurationFields.cooldown} onChange={(event) => setConfigurationFields({ ...configurationFields, cooldown: event.target.value })} className={inputClass} /></Field><Field label="Resolve after clean windows"><input type="number" min="1" max="20" value={configurationFields.cleanWindows} onChange={(event) => setConfigurationFields({ ...configurationFields, cleanWindows: event.target.value })} className={inputClass} /></Field><button className={`mt-5 w-full ${primaryButtonClass}`}>Create automatic check</button></form>
      <div className="min-w-0 space-y-6"><div><h2 className="font-semibold">Configurations</h2><ul className="mt-3 grid gap-3 sm:grid-cols-2">{evidence.configurations.map((item) => <li key={item.id} className="border bg-surface p-4"><div className="flex justify-between gap-3"><div><p className="font-semibold">{item.name}</p><p className="mt-1 text-xs text-muted">{item.baseline_strategy.replaceAll("_", " ")} · cooldown {item.cooldown_seconds}s · resolve after {item.resolve_after_clean_windows} clean</p></div><button onClick={async () => { await updateAutomaticConfiguration(item.id, !item.is_enabled); setRefresh((value) => value + 1); }} className={buttonClass}>{item.is_enabled ? "Disable" : "Enable"}</button></div></li>)}</ul></div>
        <div><h2 className="font-semibold">Check history</h2><div className="mt-3 overflow-x-auto"><table className="min-w-full text-left text-sm"><caption className="sr-only">Automatic drift check history</caption><thead><tr className="border-b"><th scope="col" className="p-3">Result</th><th scope="col" className="p-3">Baseline / current</th><th scope="col" className="p-3">Classification</th><th scope="col" className="p-3">Attempts</th><th scope="col" className="p-3">Event delivery</th></tr></thead><tbody>{evidence.checks.map((check) => { const comparison = evidence.comparisons.find((item) => item.id === check.drift_comparison_id); return <tr id={`check-${check.id}`} key={check.id} className="border-b bg-surface"><td className="p-3 font-semibold">{check.drift_comparison_id ? <Link href={`?comparison=${encodeURIComponent(check.drift_comparison_id)}`} className="text-accent hover:underline">{automaticCheckLabel(check)}</Link> : automaticCheckLabel(check)}</td><td className="break-all p-3 font-mono text-xs">{check.baseline_snapshot_id || "Not available"} / {check.current_snapshot_id}</td><td className="p-3">{comparison ? driftClassificationLabel(comparison.classification) : "Not available"}</td><td className="p-3">{check.attempt_count}</td><td className="p-3">{check.event_suppressed ? `Suppressed — ${check.event_suppression_reason || "reason unavailable"}` : "Not suppressed"}</td></tr>; })}</tbody></table></div></div>
        <div><h2 className="font-semibold">Related incidents</h2><ul className="mt-3 grid gap-3 sm:grid-cols-2">{evidence.incidents.map((incident) => <li key={incident.id} className="border bg-surface p-4"><div className="flex justify-between gap-3"><span className="font-semibold">{incidentStatusLabel(incident.status)}</span><span className="font-mono text-xs">×{incident.occurrence_count}</span></div><Link href={buildIncidentHref(incident.id)} className="mt-3 inline-block text-sm text-accent hover:underline">Open incident timeline</Link></li>)}</ul></div>
      </div>
    </section>
  </div>;
}

function ComparisonFindings({ comparison }: { comparison: DriftComparison }) { return <div className="mt-4 border bg-surface p-4"><p className="font-semibold">Latest result: {driftClassificationLabel(comparison.classification)}</p><div className="mt-3 overflow-x-auto"><table className="min-w-full text-left text-xs"><caption className="sr-only">Drift comparison findings</caption><thead><tr className="border-b"><th scope="col" className="p-2">Metric</th><th scope="col" className="p-2">Baseline</th><th scope="col" className="p-2">Current</th><th scope="col" className="p-2">Δ absolute</th><th scope="col" className="p-2">Δ relative</th><th scope="col" className="p-2">Samples</th><th scope="col" className="p-2">p value</th><th scope="col" className="p-2">Decision</th></tr></thead><tbody>{comparison.findings.map((finding) => <tr key={finding.rule_position} className="border-b"><td className="p-2 font-mono">{finding.metric}</td><td className="p-2">{formatOptionalMetric(finding.baseline_value)}</td><td className="p-2">{formatOptionalMetric(finding.current_value)}</td><td className="p-2">{formatOptionalMetric(finding.absolute_delta)}</td><td className="p-2">{formatOptionalMetric(finding.relative_delta)}</td><td className="p-2">{finding.baseline_sample_count} / {finding.current_sample_count}</td><td className="p-2">{formatOptionalMetric(finding.p_value)}</td><td className="p-2 font-semibold">{driftClassificationLabel(finding.classification)}</td></tr>)}</tbody></table></div></div>; }
function SnapshotSelect({ label, value, items, onChange }: { label: string; value: string; items: MonitoringSnapshot[]; onChange: (value: string) => void }) { return <Field label={label}><select required value={value} onChange={(event) => onChange(event.target.value)} className={inputClass}><option value="">Choose snapshot</option>{items.map((item) => <option key={item.id} value={item.id}>{formatTimestamp(item.window_start)} → {formatTimestamp(item.window_end)}</option>)}</select></Field>; }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="mt-3 grid gap-1 text-xs font-semibold text-muted">{label}{children}</label>; }
function message(reason: unknown) { return reason instanceof Error ? reason.message : "The monitoring request failed."; }
