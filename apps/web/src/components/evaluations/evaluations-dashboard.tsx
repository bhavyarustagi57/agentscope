"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/product-context";

import {
  createEvaluationRun,
  EvaluationApiError,
  listEvaluationDefinitions,
  listEvaluationRuns,
  type EvaluationDefinition,
  type EvaluationRun,
} from "@/lib/evaluation-api";
import { evaluatorKindLabel, formatConfigValue, formatProgress, formatScore } from "@/lib/evaluation-ui";
import { formatTimestamp } from "@/lib/trace-explorer";

import { DefinitionForm } from "./definition-form";
import { RunStatusBadge } from "./evaluation-status";

export function EvaluationsDashboard() {
  const router = useRouter();
  const [definitions, setDefinitions] = useState<EvaluationDefinition[]>([]);
  const [runs, setRuns] = useState<EvaluationRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [creatingRun, setCreatingRun] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback((signal: AbortSignal) => {
    Promise.all([listEvaluationDefinitions(signal), listEvaluationRuns(signal)])
      .then(([definitionPage, runPage]) => {
        setDefinitions(definitionPage.items);
        setRuns(runPage.items);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load evaluations.");
      })
      .finally(() => {
        if (!signal.aborted) setLoading(false);
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load, retry]);

  async function startRun(definition: EvaluationDefinition) {
    setCreatingRun(definition.id);
    setActionError(null);
    try {
      const run = await createEvaluationRun(definition.id);
      router.push(`/evaluations/runs/${encodeURIComponent(run.id)}`);
    } catch (reason) {
      setActionError(reason instanceof EvaluationApiError ? reason.message : "Unable to create the run.");
      setCreatingRun(null);
    }
  }

  return (
    <div className="mx-auto max-w-[90rem]">
      <PageHeader eyebrow="EVALUATE / EVALUATIONS" title="Evaluations" description="Define deterministic checks, create immutable execution attempts, and inspect evidence linked to the traces that produced it." />

      {error ? (
        <State title="Couldn’t load evaluations" detail={error} role="alert">
          <button onClick={() => { setLoading(true); setRetry((value) => value + 1); }} className="mt-4 min-h-11 border bg-white px-4 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-accent">Retry</button>
        </State>
      ) : loading ? (
        <div aria-busy="true" aria-label="Loading evaluations" className="mt-6 grid gap-5 lg:grid-cols-2"><div className="h-96 animate-pulse border bg-surface" /><div className="h-96 animate-pulse border bg-surface" /></div>
      ) : (
        <>
          <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(20rem,0.85fr)_minmax(28rem,1.15fr)]">
            <DefinitionForm onCreated={(definition) => setDefinitions((current) => [definition, ...current])} />
            <section aria-labelledby="definitions-title" className="min-w-0">
              <div className="flex items-end justify-between gap-3">
                <div><h2 id="definitions-title" className="text-lg font-semibold">Definitions</h2><p className="mt-1 text-sm text-muted">Reusable specifications; runs keep immutable snapshots.</p></div>
                <span className="font-mono text-xs text-muted">{definitions.length} LOADED</span>
              </div>
              {actionError && <p role="alert" className="mt-3 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900">{actionError}</p>}
              {definitions.length === 0 ? (
                <State title="No definitions yet" detail="Create the first deterministic definition using the form." />
              ) : (
                <ol className="mt-4 divide-y border bg-surface" aria-label="Evaluation definitions">
                  {definitions.map((definition) => (
                    <li key={definition.id} className="p-4">
                      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">{definition.name}</h3><span className="border bg-canvas px-2 py-1 text-xs">{evaluatorKindLabel(definition.evaluator_kind)}</span></div>
                          {definition.description && <p className="mt-2 text-sm leading-6 text-muted">{definition.description}</p>}
                          <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted">
                            {Object.entries(definition.evaluator_config).map(([key, value]) => <div key={key}><dt className="font-semibold text-ink">{key.replaceAll("_", " ")}</dt><dd className="mt-0.5 max-w-72 break-words font-mono">{formatConfigValue(value)}</dd></div>)}
                          </dl>
                        </div>
                        <button onClick={() => startRun(definition)} disabled={!definition.is_enabled || creatingRun === definition.id} className="min-h-11 shrink-0 border bg-white px-4 text-sm font-semibold outline-none hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-accent">
                          {creatingRun === definition.id ? "Creating…" : definition.is_enabled ? "Create run" : "Disabled"}
                        </button>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          </div>

          <section aria-labelledby="runs-title" className="mt-10 border-t pt-7">
            <div className="flex items-end justify-between gap-3"><div><h2 id="runs-title" className="text-lg font-semibold">Recent runs</h2><p className="mt-1 text-sm text-muted">One immutable execution attempt per row.</p></div><span className="font-mono text-xs text-muted">{runs.length} LOADED</span></div>
            {runs.length === 0 ? <State title="No runs yet" detail="Create a run from an enabled definition, then select traces for execution." /> : (
              <ol className="mt-4 divide-y border bg-surface" aria-label="Evaluation runs">
                {runs.map((run) => <li key={run.id}><Link href={`/evaluations/runs/${encodeURIComponent(run.id)}`} className="grid gap-4 p-4 outline-none hover:bg-canvas/70 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent lg:grid-cols-[minmax(14rem,1fr)_minmax(16rem,1fr)_auto] lg:items-center"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="truncate font-semibold">{run.definition_name}</h3><RunStatusBadge status={run.status} /></div><p className="mt-2 text-xs text-muted">{evaluatorKindLabel(run.evaluator_kind)} · {formatTimestamp(run.created_at)}</p></div><p className="text-sm text-muted">{formatProgress(run)}</p><dl className="grid grid-cols-4 gap-2 text-center text-xs"><Metric label="Passed" value={run.passed_count} /><Metric label="Failed" value={run.failed_count} /><Metric label="Errors" value={run.error_count} /><Metric label="Avg" value={formatScore(run.average_score)} /></dl></Link></li>)}
              </ol>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) { return <div className="border bg-white px-2 py-2"><dt className="text-muted">{label}</dt><dd className="mt-1 font-mono font-semibold">{value}</dd></div>; }
function State({ title, detail, role = "status", children }: { title: string; detail: string; role?: "status" | "alert"; children?: React.ReactNode }) { return <div role={role} className="mt-4 border bg-surface px-6 py-10 text-center"><h3 className="font-semibold">{title}</h3><p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-muted">{detail}</p>{children}</div>; }
