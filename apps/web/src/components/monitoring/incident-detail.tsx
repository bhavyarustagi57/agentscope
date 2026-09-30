"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Breadcrumbs } from "@/components/product-context";
import { CopyableReference } from "@/components/workflow-controls";

import {
  acknowledgeIncident,
  getDriftComparison,
  getMonitoringIncident,
  listIncidentEvents,
  type DriftComparison,
  type MonitoringIncident,
  type MonitoringIncidentEvent,
} from "@/lib/monitoring-api";
import { driftClassificationLabel, eventTypeLabel, formatOptionalMetric, incidentStatusLabel, sortIncidentEvents } from "@/lib/monitoring-ui";
import { formatTimestamp } from "@/lib/trace-explorer";
import { Metric, ViewState, buttonClass, primaryButtonClass } from "@/components/experiments/shared";

export function IncidentDetail({ incidentId }: { incidentId: string }) {
  const [incident, setIncident] = useState<MonitoringIncident | null>(null); const [events, setEvents] = useState<MonitoringIncidentEvent[]>([]);
  const [comparisons, setComparisons] = useState<DriftComparison[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState<string | null>(null); const [notice, setNotice] = useState<string | null>(null); const [acknowledging, setAcknowledging] = useState(false); const [refresh, setRefresh] = useState(0);
  useEffect(() => { const controller = new AbortController();
    Promise.all([getMonitoringIncident(incidentId, controller.signal), listIncidentEvents(incidentId, controller.signal)])
      .then(async ([incidentValue, eventPage]) => { const ids = [...new Set([incidentValue.first_drift_comparison_id, incidentValue.latest_drift_comparison_id, incidentValue.resolving_comparison_id, ...eventPage.items.map((event) => event.drift_comparison_id)].filter((value): value is string => value !== null))]; const comparisonValues = await Promise.all(ids.map((id) => getDriftComparison(id, controller.signal))); setIncident(incidentValue); setEvents(sortIncidentEvents(eventPage.items)); setComparisons(comparisonValues); setError(null); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load incident."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort();
  }, [incidentId, refresh]);

  if (loading && !incident) return <div aria-busy="true" role="status" aria-label="Loading incident" className="h-[34rem] animate-pulse border bg-surface" />;
  if ((error && !incident) || !incident) return <ViewState title="Couldn’t load incident" detail={error || "Incident unavailable."} role="alert" headingLevel="h1"><Link href="/monitoring" className={`mt-4 ${buttonClass}`}>Back to monitoring</Link></ViewState>;

  async function acknowledge() { setAcknowledging(true); setError(null); setNotice(null); try { await acknowledgeIncident(incidentId); setNotice("Incident acknowledged. Resolution still requires the configured clean windows."); setRefresh((value) => value + 1); } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to acknowledge incident."); } finally { setAcknowledging(false); } }

  return <div className="mx-auto max-w-[90rem] break-words">
    <Breadcrumbs items={[{ label: "Monitoring", href: "/monitoring" }, { label: "Monitor evidence", href: `/monitoring/${encodeURIComponent(incident.monitoring_definition_id)}` }, { label: "Incident" }]} />
    <header className="border-b pb-6"><div className="flex flex-wrap items-start justify-between gap-4"><div><p className="font-mono text-xs font-semibold tracking-[0.16em] text-accent">MONITORING INCIDENT</p><h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">{incidentStatusLabel(incident.status)} incident</h1><div className="mt-2 text-muted"><CopyableReference value={incident.id} label="incident ID" /></div></div>{incident.status === "open" && <button disabled={acknowledging} onClick={acknowledge} className={primaryButtonClass}>{acknowledging ? "Acknowledging…" : "Acknowledge"}</button>}</div><p className="mt-4 max-w-3xl border-l-4 border-amber-400 pl-3 text-sm leading-6 text-muted">Acknowledgement means the incident has been seen. It does not mean the drift is resolved. Resolution requires the configured number of consecutive clean automatic windows.</p></header>
    {notice && <p role="status" aria-live="polite" className="mt-5 border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-950">{notice}</p>}{error && <p role="alert" className="mt-5 border border-red-300 bg-red-50 p-3 text-sm text-red-900">{error}</p>}
    <section className="mt-7 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-7"><Metric label="Status" value={incidentStatusLabel(incident.status)} /><Metric label="Latest classification" value={incident.latest_classification.replaceAll("_", " ")} /><Metric label="Occurrences" value={incident.occurrence_count} /><Metric label="Consecutive clean" value={incident.consecutive_clean_count} /><Metric label="Opened" value={formatTimestamp(incident.opened_at)} /><Metric label="Acknowledged" value={incident.acknowledged_at ? formatTimestamp(incident.acknowledged_at) : "Not available"} /><Metric label="Resolved" value={incident.resolved_at ? formatTimestamp(incident.resolved_at) : "Not available"} /></section><dl className="mt-3 grid gap-2 border bg-surface p-3 text-xs sm:grid-cols-3"><EvidenceId label="First comparison" value={incident.first_drift_comparison_id} /><EvidenceId label="Latest comparison" value={incident.latest_drift_comparison_id} /><EvidenceId label="Resolving comparison" value={incident.resolving_comparison_id} /></dl>
    <section className="mt-10 grid gap-6 lg:grid-cols-[20rem_minmax(0,1fr)]"><div><h2 className="font-semibold">Lifecycle timeline</h2><p className="mt-1 text-sm leading-6 text-muted">Durable events ordered by server timestamp. No client-side lifecycle inference is applied.</p></div><ol className="border-l-2 border-slate-300 pl-6">{events.map((event) => <li key={event.id} className="relative border-b py-5 before:absolute before:-left-[1.95rem] before:top-6 before:h-3 before:w-3 before:rounded-full before:bg-accent"><div className="flex flex-wrap justify-between gap-2"><h3 className="font-semibold">{eventTypeLabel(event.event_type)}</h3><time className="font-mono text-xs text-muted">{formatTimestamp(event.created_at)}</time></div><div className="mt-2 flex flex-wrap gap-3 text-xs">{event.drift_comparison_id && <Link href={`/monitoring/${encodeURIComponent(incident.monitoring_definition_id)}?comparison=${encodeURIComponent(event.drift_comparison_id)}`} className="text-accent hover:underline">Comparison evidence</Link>}{event.automatic_drift_check_id && <Link href={`/monitoring/${encodeURIComponent(incident.monitoring_definition_id)}?check=${encodeURIComponent(event.automatic_drift_check_id)}#check-${encodeURIComponent(event.automatic_drift_check_id)}`} className="text-accent hover:underline">Automatic check</Link>}</div></li>)}</ol></section>
    <section className="mt-10"><h2 className="font-semibold">Canonical comparison evidence</h2><p className="mt-1 text-sm text-muted">First, latest, and resolving comparisons are shown when distinct and available.</p><div className="mt-4 space-y-5">{comparisons.map((comparison) => <article key={comparison.id} className="border bg-surface p-4"><div className="flex flex-wrap justify-between gap-3"><div><h3 className="font-semibold">{driftClassificationLabel(comparison.classification)}</h3><p className="mt-1 text-sm text-muted">{comparison.policy_name} · {formatTimestamp(comparison.created_at)}</p></div><span className="break-all font-mono text-xs text-muted">{comparison.id}</span></div><div className="mt-4 overflow-x-auto"><table className="min-w-full text-left text-xs"><caption className="sr-only">Canonical incident comparison findings</caption><thead><tr className="border-b"><th scope="col" className="p-2">Metric</th><th scope="col" className="p-2">Baseline</th><th scope="col" className="p-2">Current</th><th scope="col" className="p-2">Absolute Δ</th><th scope="col" className="p-2">Relative Δ</th><th scope="col" className="p-2">Samples</th><th scope="col" className="p-2">p value</th><th scope="col" className="p-2">Decision</th></tr></thead><tbody>{comparison.findings.map((finding) => <tr key={finding.rule_position} className="border-b"><td className="p-2 font-mono">{finding.metric}</td><td className="p-2">{formatOptionalMetric(finding.baseline_value)}</td><td className="p-2">{formatOptionalMetric(finding.current_value)}</td><td className="p-2">{formatOptionalMetric(finding.absolute_delta)}</td><td className="p-2">{formatOptionalMetric(finding.relative_delta)}</td><td className="p-2">{finding.baseline_sample_count} / {finding.current_sample_count}</td><td className="p-2">{formatOptionalMetric(finding.p_value)}</td><td className="p-2 font-semibold">{driftClassificationLabel(finding.classification)}</td></tr>)}</tbody></table></div></article>)}</div></section>
  </div>;
}

function EvidenceId({ label, value }: { label: string; value: string | null }) { return <div className="min-w-0"><dt className="font-semibold text-muted">{label}</dt><dd className="mt-1 break-all font-mono">{value || "Not available"}</dd></div>; }
