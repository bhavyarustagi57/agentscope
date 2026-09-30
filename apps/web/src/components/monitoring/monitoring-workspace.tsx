"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { cloneElement, isValidElement, useEffect, useId, useState, type ReactElement, type ReactNode } from "react";

import {
  DRIFT_METRICS,
  INCIDENT_STATUSES,
  TRACE_STATUSES,
  WINDOW_DURATIONS,
  createDriftPolicy,
  createMonitoringDefinition,
  listAutomaticConfigurations,
  listDriftPolicies,
  listMonitoringDefinitions,
  listMonitoringIncidents,
  updateMonitoringDefinition,
  type DriftPolicy,
  type AutomaticDriftConfiguration,
  type MonitoringDefinition,
  type MonitoringIncident,
} from "@/lib/monitoring-api";
import {
  buildMonitoringHref,
  buildDriftPolicyPayload,
  buildIncidentHref,
  buildMonitorHref,
  buildMonitoringDefinitionPayload,
  incidentStatusLabel,
  moveRule,
  newDriftRule,
  parseMonitoringFilters,
  type DriftRuleFields,
} from "@/lib/monitoring-ui";
import { formatTimestamp } from "@/lib/trace-explorer";
import { ViewState, buttonClass, inputClass, primaryButtonClass } from "@/components/experiments/shared";
import { PageHeader } from "@/components/product-context";

export function MonitoringWorkspace() {
  const router = useRouter();
  const search = useSearchParams();
  const { monitor: monitorFilter, configuration: configurationFilter, status: incidentStatus } = parseMonitoringFilters(search);
  const [monitors, setMonitors] = useState<MonitoringDefinition[]>([]);
  const [policies, setPolicies] = useState<DriftPolicy[]>([]);
  const [incidents, setIncidents] = useState<MonitoringIncident[]>([]);
  const [configurations, setConfigurations] = useState<AutomaticDriftConfiguration[]>([]);
  const [loading, setLoading] = useState(true); const [error, setError] = useState<string | null>(null); const [refresh, setRefresh] = useState(0);
  const [monitorFields, setMonitorFields] = useState({ name: "", description: "", windowDuration: "1h" as const, traceName: "", traceStatus: "" as const, evaluationDefinitionId: "" });
  const [policyFields, setPolicyFields] = useState({ name: "", description: "", rules: [newDriftRule()] as DriftRuleFields[] });
  const [monitorErrors, setMonitorErrors] = useState<Record<string, string>>({});
  const [policyErrors, setPolicyErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null); const [notice, setNotice] = useState<string | null>(null); const [saving, setSaving] = useState(false);

  useEffect(() => { const controller = new AbortController();
    Promise.all([listMonitoringDefinitions(controller.signal), listDriftPolicies(controller.signal), listMonitoringIncidents({ monitorId: monitorFilter || undefined, configurationId: configurationFilter || undefined, status: incidentStatus || undefined, signal: controller.signal })])
      .then(([monitorPage, policyPage, incidentPage]) => { setMonitors(monitorPage.items); setPolicies(policyPage.items); setIncidents(incidentPage.items); setError(null); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(message(reason, "Unable to load monitoring records.")); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [configurationFilter, incidentStatus, monitorFilter, refresh]);
  useEffect(() => { if (!monitorFilter) return; const controller = new AbortController(); listAutomaticConfigurations(monitorFilter, controller.signal).then((page) => setConfigurations(page.items)).catch(() => { if (!controller.signal.aborted) setConfigurations([]); }); return () => controller.abort(); }, [monitorFilter]);

  async function submitMonitor(event: React.FormEvent) { event.preventDefault(); const built = buildMonitoringDefinitionPayload(monitorFields);
    if (Object.keys(built.errors).length) { setMonitorErrors(built.errors); setFormError("Review the highlighted monitor fields."); return; }
    setSaving(true); setMonitorErrors({}); setFormError(null); setNotice(null); try { const created = await createMonitoringDefinition(built.payload); setMonitorFields({ ...monitorFields, name: "", description: "" }); setNotice(`Monitoring definition created: ${created.name}. Open it to materialize evidence.`); setRefresh((value) => value + 1); }
    catch (reason) { setFormError(message(reason, "Unable to create monitor.")); } finally { setSaving(false); }
  }
  async function submitPolicy(event: React.FormEvent) { event.preventDefault(); const built = buildDriftPolicyPayload(policyFields);
    if (Object.keys(built.errors).length) { setPolicyErrors(built.errors); setFormError("Review the highlighted drift-policy fields."); return; }
    setSaving(true); setPolicyErrors({}); setFormError(null); setNotice(null); try { const created = await createDriftPolicy(built.payload); setPolicyFields({ name: "", description: "", rules: [newDriftRule()] }); setNotice(`Drift policy created: ${created.name}.`); setRefresh((value) => value + 1); }
    catch (reason) { setFormError(message(reason, "Unable to create drift policy.")); } finally { setSaving(false); }
  }

  return <div className="mx-auto max-w-[96rem] break-words">
    <PageHeader eyebrow="OBSERVE / MONITORING" title="Operational monitoring" description="Define closed evidence windows, materialize canonical snapshots, compare them with durable policies, and follow automatic checks into incidents." />
    {formError && <p role="alert" className="mt-5 border border-red-300 bg-red-50 p-3 text-sm text-red-900">{formError}</p>}
    {notice && <p role="status" aria-live="polite" className="mt-5 border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-950">{notice}</p>}
    <div className="mt-7 grid gap-6 xl:grid-cols-[24rem_minmax(0,1fr)]">
      <div className="space-y-6">
        <form onSubmit={submitMonitor} className="border bg-surface p-5"><h2 className="font-semibold">Create monitor</h2><p className="mt-1 text-sm text-muted">Windows use <span className="font-mono">[start, end)</span> UTC boundaries.</p>
          <Field label="Name" error={monitorErrors.name}><input required maxLength={200} value={monitorFields.name} onChange={(event) => setMonitorFields({ ...monitorFields, name: event.target.value })} className={inputClass} /></Field>
          <Field label="Description" error={monitorErrors.description}><textarea maxLength={2000} rows={3} value={monitorFields.description} onChange={(event) => setMonitorFields({ ...monitorFields, description: event.target.value })} className={`${inputClass} py-3`} /></Field>
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2"><Field label="Window"><select value={monitorFields.windowDuration} onChange={(event) => setMonitorFields({ ...monitorFields, windowDuration: event.target.value as typeof monitorFields.windowDuration })} className={inputClass}>{WINDOW_DURATIONS.map((value) => <option key={value}>{value}</option>)}</select></Field><Field label="Trace status"><select value={monitorFields.traceStatus} onChange={(event) => setMonitorFields({ ...monitorFields, traceStatus: event.target.value as typeof monitorFields.traceStatus })} className={inputClass}><option value="">Any</option>{TRACE_STATUSES.map((value) => <option key={value}>{value}</option>)}</select></Field></div>
          <Field label="Trace name" error={monitorErrors.traceName}><input maxLength={500} value={monitorFields.traceName} onChange={(event) => setMonitorFields({ ...monitorFields, traceName: event.target.value })} className={inputClass} /></Field>
          <Field label="Evaluation definition ID"><input value={monitorFields.evaluationDefinitionId} onChange={(event) => setMonitorFields({ ...monitorFields, evaluationDefinitionId: event.target.value })} className={inputClass} /></Field>
          <button disabled={saving} className={`mt-5 w-full ${primaryButtonClass}`}>{saving ? "Saving…" : "Create monitor"}</button>
        </form>
        <form onSubmit={submitPolicy} className="border bg-surface p-5"><h2 className="font-semibold">Create drift policy</h2>
          <Field label="Name" error={policyErrors.name}><input required maxLength={200} value={policyFields.name} onChange={(event) => setPolicyFields({ ...policyFields, name: event.target.value })} className={inputClass} /></Field>
          <Field label="Description" error={policyErrors.description}><textarea maxLength={2000} rows={2} value={policyFields.description} onChange={(event) => setPolicyFields({ ...policyFields, description: event.target.value })} className={`${inputClass} py-3`} /></Field>
          <div className="mt-4 space-y-3">{policyFields.rules.map((rule, index) => <fieldset key={index} className="border bg-white p-3"><legend className="px-1 text-xs font-semibold">Rule {index + 1}</legend>
            <Field label="Metric"><select value={rule.metric} onChange={(event) => changeRule(index, { metric: event.target.value as DriftRuleFields["metric"] })} className={inputClass}>{DRIFT_METRICS.map((value) => <option key={value}>{value}</option>)}</select></Field>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2"><Field label="Direction"><select value={rule.direction} onChange={(event) => changeRule(index, { direction: event.target.value as DriftRuleFields["direction"] })} className={inputClass}><option value="increase">increase</option><option value="decrease">decrease</option></select></Field><Field label="Threshold type"><select value={rule.thresholdType} onChange={(event) => changeRule(index, { thresholdType: event.target.value as DriftRuleFields["thresholdType"] })} className={inputClass}><option value="absolute">absolute</option><option value="relative">relative</option></select></Field></div>
            <Field label="Practical threshold" error={policyErrors[`rules.${index}.practicalThreshold`]}><input type="number" min="0" step="any" value={rule.practicalThreshold} onChange={(event) => changeRule(index, { practicalThreshold: event.target.value })} className={inputClass} /></Field>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2"><Field label="Baseline min" error={policyErrors[`rules.${index}.minimumBaselineSamples`]}><input type="number" min="1" value={rule.minimumBaselineSamples} onChange={(event) => changeRule(index, { minimumBaselineSamples: event.target.value })} className={inputClass} /></Field><Field label="Current min" error={policyErrors[`rules.${index}.minimumCurrentSamples`]}><input type="number" min="1" value={rule.minimumCurrentSamples} onChange={(event) => changeRule(index, { minimumCurrentSamples: event.target.value })} className={inputClass} /></Field></div>
            <div className="mt-3 flex flex-wrap gap-2"><button type="button" disabled={index === 0} aria-label={`Move rule ${index + 1} up`} onClick={() => setPolicyFields({ ...policyFields, rules: moveRule(policyFields.rules, index, -1) })} className={buttonClass}>Up</button><button type="button" disabled={index === policyFields.rules.length - 1} aria-label={`Move rule ${index + 1} down`} onClick={() => setPolicyFields({ ...policyFields, rules: moveRule(policyFields.rules, index, 1) })} className={buttonClass}>Down</button>{policyFields.rules.length > 1 && <button type="button" aria-label={`Remove rule ${index + 1}`} onClick={() => setPolicyFields({ ...policyFields, rules: policyFields.rules.filter((_, item) => item !== index) })} className={buttonClass}>Remove</button>}</div>
          </fieldset>)}</div>
          {policyErrors.rules && <p role="alert" className="mt-2 text-sm text-red-800">{policyErrors.rules}</p>}
          <button type="button" disabled={policyFields.rules.length >= 20} onClick={() => setPolicyFields({ ...policyFields, rules: [...policyFields.rules, newDriftRule()] })} className={`mt-4 ${buttonClass}`}>Add rule</button><button disabled={saving} className={`mt-4 w-full ${primaryButtonClass}`}>Create policy</button>
        </form>
      </div>
      <div className="min-w-0 space-y-8">
        <section aria-labelledby="monitor-list"><div className="flex items-end justify-between gap-3"><div><h2 id="monitor-list" className="font-semibold">Monitoring definitions</h2><p className="mt-1 text-sm text-muted">{monitors.length} configured · {policies.length} policies available</p></div><button onClick={() => setRefresh((value) => value + 1)} className={buttonClass}>Refresh</button></div>
          {loading ? <div aria-busy="true" role="status" className="mt-4 h-64 animate-pulse border bg-surface" aria-label="Loading monitoring definitions" /> : error ? <div className="mt-4"><ViewState title="Couldn’t load monitoring" detail={error} role="alert"><button type="button" onClick={() => { setLoading(true); setRefresh((value) => value + 1); }} className={`mt-4 ${primaryButtonClass}`}>Retry</button></ViewState></div> : monitors.length === 0 ? <div className="mt-4"><ViewState title="No monitors yet" detail="Create a definition to begin collecting closed-window evidence." /></div> : <ul className="mt-4 space-y-3">{monitors.map((monitor) => <li key={monitor.id} className="border bg-surface p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><Link href={buildMonitorHref(monitor.id)} className="font-semibold text-accent hover:underline">{monitor.name}</Link><p className="mt-1 text-sm text-muted">{monitor.description || "No description"}</p><p className="mt-2 break-all font-mono text-xs text-muted">{monitor.id}</p></div><button onClick={async () => { setFormError(null); try { await updateMonitoringDefinition(monitor.id, !monitor.is_enabled); setNotice(`Monitoring definition ${monitor.is_enabled ? "disabled" : "enabled"}.`); setRefresh((value) => value + 1); } catch (reason) { setFormError(message(reason, "Unable to update monitoring definition.")); } }} className={buttonClass}>{monitor.is_enabled ? "Disable" : "Enable"}</button></div><p className="mt-3 border-t pt-3 text-xs text-muted">{monitor.window_duration} windows · trace {monitor.trace_scope.trace_name || "any name"} / {monitor.trace_scope.trace_status || "any status"}</p></li>)}</ul>}
        </section>
        <section aria-labelledby="incident-list"><div className="flex flex-wrap items-end justify-between gap-3"><div><h2 id="incident-list" className="font-semibold">Incidents</h2><p className="mt-1 text-sm text-muted">Durable alert lifecycle, newest API records first.</p></div><div className="grid w-full grid-cols-1 gap-2 sm:grid-cols-3 lg:w-auto"><Field label="Monitor"><select value={monitorFilter} onChange={(event) => router.push(buildMonitoringHref({ monitor: event.target.value, configuration: "", status: incidentStatus }))} className={inputClass}><option value="">All monitors</option>{monitors.map((monitor) => <option key={monitor.id} value={monitor.id}>{monitor.name}</option>)}</select></Field><Field label="Configuration"><select disabled={!monitorFilter} value={configurationFilter} onChange={(event) => router.push(buildMonitoringHref({ monitor: monitorFilter, configuration: event.target.value, status: incidentStatus }))} className={inputClass}><option value="">All configurations</option>{configurations.map((configuration) => <option key={configuration.id} value={configuration.id}>{configuration.name}</option>)}</select></Field><Field label="Status"><select value={incidentStatus} onChange={(event) => router.push(buildMonitoringHref({ monitor: monitorFilter, configuration: configurationFilter, status: event.target.value as typeof incidentStatus }))} className={inputClass}><option value="">All statuses</option>{INCIDENT_STATUSES.map((value) => <option key={value} value={value}>{incidentStatusLabel(value)}</option>)}</select></Field></div></div>
          {(monitorFilter || configurationFilter || incidentStatus) && <div className="mt-3 flex flex-wrap items-center gap-2" aria-label="Active filters"><span className="text-xs font-semibold text-muted">Active filters: {[monitorFilter, configurationFilter, incidentStatus].filter(Boolean).length}</span><button type="button" onClick={() => router.push(buildMonitoringHref({ monitor: "", configuration: "", status: "" }))} className={buttonClass}>Clear filters</button></div>}
          <div className="mt-4 overflow-x-auto"><table className="min-w-full border-collapse text-left text-sm"><caption className="sr-only">Monitoring incidents</caption><thead><tr className="border-b"><th scope="col" className="p-3">Status</th><th scope="col" className="p-3">Opened</th><th scope="col" className="p-3">Occurrences</th><th scope="col" className="p-3">Evidence</th></tr></thead><tbody>{incidents.map((incident) => <tr key={incident.id} className="border-b bg-surface"><td className="p-3 font-semibold">{incidentStatusLabel(incident.status)}</td><td className="p-3">{formatTimestamp(incident.opened_at)}</td><td className="p-3 font-mono">{incident.occurrence_count}</td><td className="p-3"><Link href={buildIncidentHref(incident.id)} className="text-accent hover:underline">View timeline</Link></td></tr>)}</tbody></table>{!loading && incidents.length === 0 && <p className="border p-6 text-center text-sm text-muted">{monitorFilter || configurationFilter || incidentStatus ? "No incidents match these filters." : "No incidents have been recorded."}</p>}</div>
        </section>
      </div>
    </div>
  </div>;

  function changeRule(index: number, patch: Partial<DriftRuleFields>) { setPolicyFields({ ...policyFields, rules: policyFields.rules.map((rule, item) => item === index ? { ...rule, ...patch } : rule) }); }
}

function Field({ label, error, children }: { label: string; error?: string; children: ReactNode }) { const id = useId(); const control = isValidElement(children) ? cloneElement(children as ReactElement<{ "aria-describedby"?: string; "aria-invalid"?: boolean }>, { "aria-describedby": error ? `${id}-error` : undefined, "aria-invalid": Boolean(error) }) : children; return <label className="mt-3 grid gap-1 text-xs font-semibold text-muted">{label}{control}{error && <span id={`${id}-error`} role="alert" className="text-sm font-normal text-red-800">{error}</span>}</label>; }
function message(reason: unknown, fallback: string) { return reason instanceof Error ? reason.message : fallback; }
