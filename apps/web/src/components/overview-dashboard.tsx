"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PageHeader, ProductStatus } from "@/components/product-context";
import { CopyableCommand } from "@/components/workflow-controls";
import { listJudgeRuns } from "@/lib/calibration-api";
import { listEvaluationRuns } from "@/lib/evaluation-api";
import { listExperiments } from "@/lib/experiment-api";
import { listMonitoringIncidents } from "@/lib/monitoring-api";
import { hasDemoEvidence, isEmptyOverview, settleOverviewSources, summarizeMonitoringIncidents, type OverviewResult } from "@/lib/product-overview";
import { overviewAvailability } from "@/lib/product-ui";
import { listRegressionChecks } from "@/lib/regression-api";
import { listTraces } from "@/lib/trace-api";

const areas = [
  { key: "traces", title: "Traces", href: "/traces", description: "Recent execution evidence", empty: "No traces captured yet." },
  { key: "evaluations", title: "Evaluations", href: "/evaluations", description: "Deterministic evaluation runs", empty: "No evaluation runs yet." },
  { key: "calibration", title: "Calibration", href: "/calibration", description: "Human-aligned judge runs", empty: "No judge runs yet." },
  { key: "experiments", title: "Experiments", href: "/experiments", description: "Paired A/B evidence", empty: "No experiments yet." },
  { key: "regressions", title: "Regressions", href: "/regressions", description: "Policy checks and investigations", empty: "No regression checks yet." },
  { key: "monitoring", title: "Monitoring", href: "/monitoring", description: "Open operational incidents", empty: "No active incidents." },
] as const;

const demoPrefix = "AgentScope Demo";
const gettingStarted = [
  ["Instrument an agent", "/traces"], ["Explore traces", "/traces"],
  ["Evaluate outputs", "/evaluations"], ["Calibrate a judge", "/calibration"],
  ["Compare agent versions", "/experiments"], ["Detect regressions", "/regressions"],
  ["Monitor production", "/monitoring"],
] as const;

type Results = Record<string, OverviewResult>;

export function OverviewDashboard() {
  const [results, setResults] = useState<Results | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    settleOverviewSources({
      traces: listTraces({}, { signal }).then((page) => ({ count: page.items.length, active: page.items.filter((item) => item.status === "running").length, attention: page.items.filter((item) => item.status === "error").length, demo: page.items.some((item) => item.name.startsWith(demoPrefix)) })),
      evaluations: listEvaluationRuns(signal).then((page) => ({ count: page.items.length, active: page.items.filter((item) => ["pending", "queued", "running"].includes(item.status)).length, attention: page.items.filter((item) => item.status === "failed").length, demo: page.items.some((item) => item.definition_name.startsWith(demoPrefix)) })),
      calibration: listJudgeRuns(signal).then((page) => ({ count: page.items.length, active: page.items.filter((item) => ["pending", "queued", "running"].includes(item.status)).length, attention: page.items.filter((item) => item.status === "failed").length, demo: page.items.some((item) => item.model === "local-deterministic-demo-v1") })),
      experiments: listExperiments({ pageSize: 20, signal }).then((page) => ({ count: page.items.length, active: page.items.filter((item) => item.status === "running").length, attention: page.items.filter((item) => item.status === "failed").length, demo: page.items.some((item) => item.name.startsWith(demoPrefix)) })),
      regressions: listRegressionChecks({ signal }).then((page) => ({ count: page.items.length, attention: page.items.filter((item) => item.classification === "regression_detected").length, demo: page.items.some((item) => item.policy_name.startsWith(demoPrefix)) })),
      monitoring: listMonitoringIncidents({ signal }).then((page) => summarizeMonitoringIncidents(page.items)),
    }).then((value) => { if (!signal.aborted) setResults(value); });
    return () => controller.abort();
  }, [retry]);

  const summary = results ? overviewAvailability(Object.values(results)) : null;
  const failed = results ? Object.values(results).filter((result) => result.state === "error").length : 0;
  const empty = results ? isEmptyOverview(Object.values(results)) : false;
  const demo = results ? hasDemoEvidence(Object.values(results)) : false;

  return <div className="mx-auto max-w-[90rem]">
    <PageHeader eyebrow="WORKSPACE / OVERVIEW" title="Operational evidence" description="Start with the evidence that exists now, then move into evaluation, comparison, or investigation without losing context." actions={summary && <div className="border bg-surface px-4 py-3 text-sm"><span className="font-semibold">{summary.available} of {summary.total}</span> evidence sources available</div>} />
    {!results ? <div className="mt-8 border bg-surface p-8" role="status"><p className="font-semibold">Loading evidence…</p><p className="mt-2 text-sm text-muted">Reading one bounded page from each product area.</p></div> : <>
      {failed > 0 && <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border border-amber-300 bg-amber-50 p-4" role="status"><p className="text-sm"><strong>{failed} source{failed === 1 ? " is" : "s are"} unavailable.</strong> Available evidence remains visible.</p><button type="button" onClick={() => { setResults(null); setRetry((value) => value + 1); }} className="min-h-11 border border-amber-700 bg-white px-4 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-accent">Retry unavailable sources</button></div>}
      {demo && <div className="mt-6 border border-sky-300 bg-sky-50 p-4" role="status"><p className="text-sm"><strong>Sample evidence is loaded.</strong> Entities labeled AgentScope Demo are synthetic and must not be treated as production evidence.</p></div>}
      {empty && <section aria-labelledby="getting-started-title" className="mt-8 border bg-surface p-6"><h2 id="getting-started-title" className="text-xl font-semibold">Getting started</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-muted">Instrument your own agent, or load the deterministic local sample workspace from the repository root. The command runs locally and does not call a paid provider.</p><CopyableCommand value="uv run --project apps/api python apps/api/scripts/seed_demo.py" /><ol className="mt-5 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{gettingStarted.map(([label, href], index) => <li key={label}><Link href={href} className="inline-flex min-h-11 items-center text-sm font-semibold text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent"><span className="mr-2 text-muted">{index + 1}.</span>{label}</Link></li>)}</ol></section>}
      <section aria-labelledby="evidence-map-title" className="mt-8"><div><h2 id="evidence-map-title" className="text-xl font-semibold">Evidence map</h2><p className="mt-1 text-sm text-muted">Counts reflect bounded pages returned by existing APIs, not all-time totals.</p></div>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{areas.map((area) => {
          const result = results[area.key];
          return <article key={area.key} className="flex min-h-56 flex-col border bg-surface p-5"><div className="flex items-start justify-between gap-3"><div><h3 className="text-lg font-semibold">{area.title}</h3><p className="mt-1 text-sm text-muted">{area.description}</p></div><ProductStatus status={result.state === "error" ? "Unavailable" : result.state === "empty" ? "No evidence yet" : result.demo ? "Demo evidence" : "Available"} /></div>
            {result.state === "error" ? <p className="mt-7 text-sm text-muted">{result.message} Open the workspace for its full recovery options.</p> : result.state === "empty" ? <p className="mt-7 text-sm text-muted">{area.empty} Open the workspace to create or capture the first record.</p> : <dl className="mt-7 grid grid-cols-3 gap-3"><Metric label="Loaded" value={result.count} /><Metric label="Active" value={result.active ?? 0} /><Metric label="Attention" value={result.attention ?? 0} /></dl>}
            <Link href={area.href} className="mt-auto inline-flex min-h-11 items-center pt-5 text-sm font-semibold text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent">Open {area.title}<span aria-hidden="true"> →</span></Link>
          </article>;
        })}</div>
      </section>
      <section aria-labelledby="next-step-title" className="mt-10 border-t pt-7"><h2 id="next-step-title" className="text-xl font-semibold">Choose the next investigation</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-muted">Start in Traces for execution detail, Evaluations or Calibration for quality evidence, Experiments for paired comparison, Regressions for practical-drop investigation, or Monitoring for current incidents.</p></section>
    </>}
  </div>;
}

function Metric({ label, value }: Readonly<{ label: string; value: number }>) {
  return <div><dt className="text-xs text-muted">{label}</dt><dd className="mt-1 text-2xl font-semibold tabular-nums">{value}</dd></div>;
}
