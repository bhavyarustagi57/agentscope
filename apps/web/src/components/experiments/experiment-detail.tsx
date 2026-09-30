"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Breadcrumbs } from "@/components/product-context";
import { ConfirmAction } from "@/components/workflow-controls";

import { createExperimentRun, ExperimentApiError, getExperiment, listExperimentRuns, markExperimentReady, type Experiment, type ExperimentRun } from "@/lib/experiment-api";
import { listEvaluationDefinitions, type EvaluationDefinition } from "@/lib/evaluation-api";
import { experimentActions } from "@/lib/experiment-ui";
import { formatTimestamp } from "@/lib/trace-explorer";

import { ExperimentConfiguration } from "./experiment-configuration";
import { StatusBadge, ViewState, buttonClass, primaryButtonClass } from "./shared";

export function ExperimentDetail({ experimentId }: { experimentId: string }) {
  const router = useRouter();
  const [experiment, setExperiment] = useState<Experiment | null>(null); const [definitions, setDefinitions] = useState<EvaluationDefinition[]>([]); const [runs, setRuns] = useState<ExperimentRun[]>([]);
  const [loading, setLoading] = useState(true); const [error, setError] = useState<{ message: string; notFound: boolean } | null>(null); const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState<"ready" | "run" | null>(null); const [actionError, setActionError] = useState<string | null>(null);
  useEffect(() => { const controller = new AbortController(); Promise.all([getExperiment(experimentId, controller.signal), listEvaluationDefinitions(controller.signal), listExperimentRuns(experimentId, controller.signal)])
    .then(([next, definitionPage, runPage]) => { setExperiment(next); setDefinitions(definitionPage.items); setRuns(runPage.items); setError(null); })
    .catch((reason: unknown) => { if (!controller.signal.aborted) setError({ message: reason instanceof Error ? reason.message : "Unable to load experiment.", notFound: reason instanceof ExperimentApiError && reason.status === 404 }); })
    .finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort();
  }, [experimentId, retry]);
  async function ready() { setBusy("ready"); setActionError(null); try { setExperiment(await markExperimentReady(experimentId)); } catch (reason) { setActionError(reason instanceof Error ? reason.message : "Unable to mark experiment ready."); } finally { setBusy(null); } }
  async function createRun() { if (busy) return; setBusy("run"); setActionError(null); try { const run = await createExperimentRun(experimentId); router.push(`/experiments/runs/${encodeURIComponent(run.id)}`); } catch (reason) { setActionError(reason instanceof Error ? reason.message : "Unable to create run."); setBusy(null); } }
  if (loading) return <div aria-busy="true" aria-label="Loading experiment detail" className="mx-auto h-[36rem] max-w-[90rem] animate-pulse border bg-surface" />;
  if (error) return <ViewState title={error.notFound ? "Experiment not found" : "Couldn’t load experiment"} detail={error.notFound ? "This experiment ID does not exist or is no longer available." : error.message} role="alert" headingLevel="h1"><div className="mt-5 flex justify-center gap-3"><Link href="/experiments" className={buttonClass}>Back to experiments</Link>{!error.notFound && <button onClick={() => setRetry((value) => value + 1)} className={primaryButtonClass}>Retry</button>}</div></ViewState>;
  if (!experiment) return null; const actions = experimentActions(experiment.status);
  return <div className="mx-auto max-w-[90rem]"><Breadcrumbs items={[{ label: "Experiments", href: "/experiments" }, { label: experiment.name }]} />
    <header className="border-b pb-6"><p className="font-mono text-xs font-semibold tracking-[0.16em] text-accent">EXPERIMENT / DETAIL</p><div className="mt-3 flex flex-wrap items-start justify-between gap-4"><div><div className="flex flex-wrap items-center gap-3"><h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">{experiment.name}</h1><StatusBadge status={experiment.status} /></div><p className="mt-2 break-all font-mono text-xs text-muted">{experiment.id}</p></div><div className="flex flex-wrap gap-3">{actions.canReady && experiment.subjects.length > 0 && experiment.evaluation_conditions.length > 0 && <ConfirmAction triggerLabel="Freeze and mark READY" confirmLabel="Confirm READY freeze" description="This permanently freezes variants, provenance, subject ordering, and evaluation conditions. Create a new experiment if these inputs must change." busy={busy !== null} onConfirm={ready} />}{actions.canCreateRun && <button onClick={createRun} disabled={busy !== null} className={primaryButtonClass}>{busy === "run" ? "Creating…" : "Create run"}</button>}</div></div>
      {actions.canReady && <p className="mt-4 max-w-4xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-950">READY freezes variants, provenance, subject ordering, and evaluation conditions. Review the saved draft carefully; this action cannot be undone.</p>}{actionError && <p role="alert" className="mt-4 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">{actionError}</p>}
    </header>
    <div className="mt-7"><ExperimentConfiguration key={experiment.updated_at} experiment={experiment} definitions={definitions} onSaved={setExperiment} /></div>
    <section aria-labelledby="history-title" className="mt-9 border-t pt-7"><div><h2 id="history-title" className="text-lg font-semibold">Historical runs</h2><p className="mt-1 text-sm leading-6 text-muted">Each run is an independent durable record. Prior evidence is never overwritten or automatically compared.</p></div>{runs.length === 0 ? <ViewState title="No runs yet" detail={actions.canCreateRun ? "Create a run from this frozen experiment when you are ready to execute it." : "Runs become available after the experiment is marked READY."} /> : <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[48rem] border-collapse text-left text-sm"><thead><tr className="border bg-slate-50"><Header>Run</Header><Header>Status</Header><Header>Progress</Header><Header>Created</Header><Header>Executed / completed</Header></tr></thead><tbody>{runs.map((run) => <tr key={run.id} className="border"><td className="p-3"><Link href={`/experiments/runs/${encodeURIComponent(run.id)}`} className="break-all font-mono text-xs font-semibold text-accent hover:underline">{run.id}</Link></td><td className="p-3"><StatusBadge status={run.status} /></td><td className="p-3 font-mono text-xs">{run.completed_decision_count} / {run.expected_decision_count}</td><td className="p-3">{formatTimestamp(run.created_at)}</td><td className="p-3">{formatTimestamp(run.completed_at ?? run.started_at ?? run.queued_at)}</td></tr>)}</tbody></table></div>}</section>
  </div>;
}
function Header({ children }: { children: React.ReactNode }) { return <th scope="col" className="p-3 text-xs font-semibold text-muted">{children}</th>; }
