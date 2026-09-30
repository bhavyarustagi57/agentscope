"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { CopyableReference } from "@/components/workflow-controls";
import {
  EvaluationApiError,
  getEvaluationRun,
  listEvaluationResults,
  type EvaluationExecutionAccepted,
  type EvaluationResult,
  type EvaluationRun,
} from "@/lib/evaluation-api";
import {
  evaluatorKindLabel,
  formatConfigValue,
  formatScore,
  parseOutcomeFilter,
  parseResultOffset,
  runStatusMessage,
  shouldPollRun,
} from "@/lib/evaluation-ui";
import { formatTimestamp } from "@/lib/trace-explorer";

import { RunStatusBadge } from "./evaluation-status";
import { ResultList } from "./result-list";
import { TraceSelector } from "./trace-selector";

export function EvaluationRunDetail({ runId }: { runId: string }) {
  const searchParams = useSearchParams();
  const outcome = parseOutcomeFilter(searchParams.get("outcome"));
  const offset = parseResultOffset(searchParams.get("offset"));
  const [run, setRun] = useState<EvaluationRun | null>(null);
  const [results, setResults] = useState<EvaluationResult[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadedResultsKey, setLoadedResultsKey] = useState<string | null>(null);
  const [error, setError] = useState<{ message: string; notFound: boolean } | null>(null);
  const [resultsError, setResultsError] = useState<string | null>(null);
  const [pollWarning, setPollWarning] = useState<string | null>(null);
  const [deliveryMessage, setDeliveryMessage] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [resultsRetry, setResultsRetry] = useState(0);
  const resultsKey = `${outcome ?? "all"}:${offset}`;
  const runStatus = run?.status;

  const refreshRun = useCallback(async (signal?: AbortSignal) => {
    const next = await getEvaluationRun(runId, signal);
    setRun(next);
    return next;
  }, [runId]);

  useEffect(() => {
    const controller = new AbortController();
    getEvaluationRun(runId, controller.signal)
      .then((next) => { setRun(next); setError(null); })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError({
          message: reason instanceof Error ? reason.message : "Unable to load this run.",
          notFound: reason instanceof EvaluationApiError && reason.status === 404,
        });
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [runId, retry]);

  useEffect(() => {
    const controller = new AbortController();
    listEvaluationResults(runId, { outcome, offset, pageSize: 20, signal: controller.signal })
      .then((page) => { setResults(page.items); setHasMore(page.has_more); setResultsError(null); setLoadedResultsKey(resultsKey); })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) { setResultsError(reason instanceof Error ? reason.message : "Unable to load results."); setLoadedResultsKey(resultsKey); }
      });
    return () => controller.abort();
  }, [runId, outcome, offset, run?.result_count, resultsKey, resultsRetry]);

  useEffect(() => {
    if (!runStatus || !shouldPollRun(runStatus)) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    const poll = async () => {
      try {
        const next = await refreshRun(controller.signal);
        setPollWarning(null);
        if (!shouldPollRun(next.status)) return;
      } catch (reason) {
        if (!controller.signal.aborted) setPollWarning(reason instanceof Error ? reason.message : "Status refresh was interrupted.");
      }
      if (!stopped) timer = setTimeout(poll, 3_000);
    };
    timer = setTimeout(poll, 3_000);
    return () => {
      stopped = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [runStatus, refreshRun]);

  async function submitted(response: EvaluationExecutionAccepted) {
    setDeliveryMessage(response.queue_delivery === "deferred"
      ? "Execution is durably queued. Delivery was deferred and worker recovery will re-enqueue it."
      : "Execution submitted. Status will refresh automatically.");
    try { await refreshRun(); } catch { setPollWarning("The run was submitted, but its latest status could not be refreshed yet."); }
  }

  if (loading) return <div aria-busy="true" aria-label="Loading evaluation run" className="mx-auto h-[32rem] max-w-[90rem] animate-pulse border bg-surface" />;
  if (error) return <State title={error.notFound ? "Evaluation run not found" : "Couldn’t load evaluation run"} detail={error.notFound ? "This run ID does not exist or is no longer available." : error.message}><div className="mt-5 flex flex-wrap justify-center gap-3"><Link href="/evaluations" className="min-h-11 border bg-white px-4 py-3 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-accent">Back to evaluations</Link>{!error.notFound && <button onClick={() => { setLoading(true); setRetry((value) => value + 1); }} className="min-h-11 bg-panel px-4 text-sm font-semibold text-white focus-visible:ring-2 focus-visible:ring-accent">Retry</button>}</div></State>;
  if (!run) return null;

  return (
    <div className="mx-auto max-w-[90rem]">
      <Link href="/evaluations" className="text-sm font-semibold text-accent underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-accent">← Back to evaluations</Link>
      <header className="mt-5 border-b pb-6">
        <p className="font-mono text-xs font-semibold tracking-[0.16em] text-accent">EVALUATION / RUN</p>
        <div className="mt-3 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between"><div className="min-w-0"><div className="flex flex-wrap items-center gap-3"><h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">{run.definition_name}</h1><RunStatusBadge status={run.status} /></div><div className="mt-2 text-muted"><CopyableReference value={run.id} label="evaluation run ID" /></div></div><span className="border bg-surface px-3 py-2 text-sm font-semibold">{evaluatorKindLabel(run.evaluator_kind)}</span></div>
        <p role="status" aria-live="polite" className="mt-4 text-sm leading-6 text-muted">{runStatusMessage(run)}</p>
        {deliveryMessage && shouldPollRun(run.status) && <p role="status" className="mt-3 border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950">{deliveryMessage}</p>}
        {pollWarning && <p role="status" className="mt-3 border bg-surface px-4 py-3 text-sm text-muted">Automatic status refresh was interrupted: {pollWarning} Retrying shortly.</p>}
      </header>

      <section aria-labelledby="summary-title" className="mt-7"><h2 id="summary-title" className="text-lg font-semibold">Run summary</h2><div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7"><Metric label="Subjects" value={run.subject_count} /><Metric label="Persisted" value={run.result_count} /><Metric label="Passed" value={run.passed_count} /><Metric label="Failed" value={run.failed_count} /><Metric label="Errors" value={run.error_count} /><Metric label="Scored" value={run.scored_count} /><Metric label="Average" value={formatScore(run.average_score)} /></div></section>

      <div className="mt-7 grid gap-6 lg:grid-cols-2">
        <section aria-labelledby="snapshot-title" className="border bg-surface p-5"><h2 id="snapshot-title" className="font-semibold">Definition snapshot</h2><p className="mt-1 text-sm text-muted">Immutable configuration used by this run.</p><dl className="mt-4 grid gap-3 sm:grid-cols-2">{Object.entries(run.evaluator_config).map(([key, value]) => <div key={key}><dt className="text-xs font-semibold text-muted">{key.replaceAll("_", " ")}</dt><dd className="mt-1 break-words text-sm">{formatConfigValue(value)}</dd></div>)}</dl></section>
        <section aria-labelledby="lifecycle-title" className="border bg-surface p-5"><h2 id="lifecycle-title" className="font-semibold">Lifecycle</h2><dl className="mt-4 grid gap-3 sm:grid-cols-2"><Time label="Created" value={run.created_at} /><Time label="Queued" value={run.queued_at} /><Time label="Started" value={run.started_at} /><Time label="Completed" value={run.completed_at} /></dl>{run.status === "failed" && <p role="alert" className="mt-4 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900">{run.error_message ?? "The run failed safely without an exposed internal error."}</p>}</section>
      </div>

      {run.status === "pending" && <TraceSelector runId={run.id} onSubmitted={submitted} />}
      {loadedResultsKey !== resultsKey ? <div aria-busy="true" aria-label="Loading evaluation results" className="mt-8 h-48 animate-pulse border bg-surface" /> : resultsError ? <State title="Couldn’t load results" detail={resultsError}><button onClick={() => { setLoadedResultsKey(null); setResultsRetry((value) => value + 1); }} className="mt-4 min-h-11 border bg-white px-4 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-accent">Retry results</button></State> : <ResultList runId={run.id} results={results} outcome={outcome} offset={offset} hasMore={hasMore} />}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) { return <dl className="border bg-surface px-3 py-3 text-center"><dt className="text-xs text-muted">{label}</dt><dd className="mt-1 font-mono text-lg font-semibold">{value}</dd></dl>; }
function Time({ label, value }: { label: string; value: string | null }) { return <div><dt className="text-xs font-semibold text-muted">{label}</dt><dd className="mt-1 text-sm">{formatTimestamp(value)}</dd></div>; }
function State({ title, detail, children }: { title: string; detail: string; children?: React.ReactNode }) { return <div role="alert" className="mx-auto mt-8 max-w-3xl border bg-surface px-6 py-12 text-center"><h1 className="text-xl font-semibold">{title}</h1><p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-muted">{detail}</p>{children}</div>; }
