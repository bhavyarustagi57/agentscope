"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
  CalibrationApiError,
  createCalibrationAnalysis,
  executeJudgeRun,
  getCalibrationAnalysis,
  getJudgeProgress,
  getJudgeRun,
  getReferenceSet,
  getStudy,
  listAllJudgeResults,
  type CalibrationAnalysis,
  type CalibrationStudy,
  type JudgeProgress,
  type JudgeResult,
  type JudgeRun,
  type ReferenceSet,
} from "@/lib/calibration-api";
import { isProviderFailure, parseDisagreementCategory, parseOffset, shouldPollJudgeRun } from "@/lib/calibration-ui";
import { formatTimestamp } from "@/lib/trace-explorer";

import { CalibrationReport } from "./calibration-report";
import { RunComparison } from "./run-comparison";
import { JudgeRunStatusBadge, Metric, ViewState, buttonClass, primaryButtonClass } from "./shared";

type RunContext = { run: JudgeRun; progress: JudgeProgress; study: CalibrationStudy; referenceSet: ReferenceSet; results: JudgeResult[] };

export function CalibrationRunDetail({ runId }: { runId: string }) {
  const searchParams = useSearchParams();
  const category = parseDisagreementCategory(searchParams.get("category"));
  const offset = parseOffset(searchParams.get("offset"));
  const [context, setContext] = useState<RunContext | null>(null);
  const [analysis, setAnalysis] = useState<CalibrationAnalysis | null>(null);
  const [analysisMissing, setAnalysisMissing] = useState(false);
  const [loading, setLoading] = useState(true); const [error, setError] = useState<{ message: string; notFound: boolean } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null); const [actionBusy, setActionBusy] = useState<"execute" | "analysis" | null>(null);
  const [pollWarning, setPollWarning] = useState<string | null>(null); const [retry, setRetry] = useState(0);

  const loadContext = useCallback(async (signal?: AbortSignal): Promise<RunContext> => {
    const run = await getJudgeRun(runId, signal);
    const [progress, study, results] = await Promise.all([
      getJudgeProgress(runId, signal), getStudy(run.study_id, signal), listAllJudgeResults(runId, signal),
    ]);
    const referenceSet = await getReferenceSet(study.reference_set_id, signal);
    return { run, progress, study, referenceSet, results };
  }, [runId]);

  const loadAnalysis = useCallback(async (signal?: AbortSignal) => {
    try { setAnalysis(await getCalibrationAnalysis(runId, signal)); setAnalysisMissing(false); }
    catch (reason) {
      if (reason instanceof CalibrationApiError && reason.status === 404) { setAnalysis(null); setAnalysisMissing(true); return; }
      throw reason;
    }
  }, [runId]);

  useEffect(() => {
    const controller = new AbortController();
    loadContext(controller.signal).then(async (next) => {
      setContext(next); setError(null);
      if (next.run.status === "completed") await loadAnalysis(controller.signal);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError({ message: reason instanceof Error ? reason.message : "Unable to load this judge run.", notFound: reason instanceof CalibrationApiError && reason.status === 404 });
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [loadAnalysis, loadContext, retry]);

  const runStatus = context?.run.status;
  useEffect(() => {
    if (!runStatus || !shouldPollJudgeRun(runStatus)) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined; let stopped = false;
    const poll = async () => {
      try {
        const [run, progress] = await Promise.all([getJudgeRun(runId, controller.signal), getJudgeProgress(runId, controller.signal)]);
        setContext((current) => current ? { ...current, run, progress } : current); setPollWarning(null);
        if (!shouldPollJudgeRun(run.status)) {
          const next = await loadContext(controller.signal); setContext(next);
          if (run.status === "completed") await loadAnalysis(controller.signal);
          return;
        }
      } catch (reason) { if (!controller.signal.aborted) setPollWarning(reason instanceof Error ? reason.message : "Status refresh was interrupted."); }
      if (!stopped) timer = setTimeout(poll, 3_000);
    };
    timer = setTimeout(poll, 3_000);
    return () => { stopped = true; controller.abort(); if (timer) clearTimeout(timer); };
  }, [runId, runStatus, loadAnalysis, loadContext]);

  async function execute() {
    setActionBusy("execute"); setActionError(null);
    try { await executeJudgeRun(runId); setContext(await loadContext()); }
    catch (reason) { setActionError(reason instanceof Error ? reason.message : "Unable to submit judge execution."); }
    finally { setActionBusy(null); }
  }
  async function generateAnalysis() {
    setActionBusy("analysis"); setActionError(null);
    try { setAnalysis(await createCalibrationAnalysis(runId)); setAnalysisMissing(false); setContext(await loadContext()); }
    catch (reason) { setActionError(reason instanceof Error ? reason.message : "Unable to create calibration analysis."); }
    finally { setActionBusy(null); }
  }

  if (loading) return <div aria-busy="true" aria-label="Loading calibration judge run" className="mx-auto h-[36rem] max-w-[90rem] animate-pulse border bg-surface" />;
  if (error) return <ViewState title={error.notFound ? "Judge run not found" : "Couldn’t load judge run"} detail={error.notFound ? "This judge-run ID does not exist or is no longer available." : error.message} role="alert" headingLevel="h1"><div className="mt-5 flex flex-wrap justify-center gap-3"><Link href="/calibration#judge-runs" className={buttonClass}>Back to calibration</Link>{!error.notFound && <button onClick={() => { setLoading(true); setRetry((value) => value + 1); }} className={primaryButtonClass}>Retry</button>}</div></ViewState>;
  if (!context) return null;
  const { run, progress, study, referenceSet, results } = context;
  const providerFailures = results.filter(isProviderFailure);

  return (
    <div className="mx-auto max-w-[90rem]">
      <Link href="/calibration#judge-runs" className="text-sm font-semibold text-accent underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-accent">← Back to calibration</Link>
      <header className="mt-5 border-b pb-6">
        <p className="font-mono text-xs font-semibold tracking-[0.16em] text-accent">CALIBRATION / JUDGE RUN</p>
        <div className="mt-3 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between"><div className="min-w-0"><div className="flex flex-wrap items-center gap-3"><h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">{run.provider} · {run.model}</h1><JudgeRunStatusBadge status={run.status} /></div><p className="mt-2 break-all font-mono text-xs text-muted">{run.id}</p></div><span className="border bg-surface px-3 py-2 text-sm font-semibold">Attempt {run.attempt_count}</span></div>
        <p role="status" aria-live="polite" className="mt-4 text-sm leading-6 text-muted">{run.status === "pending" ? "This immutable run is ready to submit." : run.status === "completed" ? "All study subjects completed successfully; calibration analysis is eligible." : run.status === "failed" ? `The run failed${run.error_category ? `: ${run.error_category.replaceAll("_", " ")}` : "."}` : `${progress.result_count} of ${progress.subject_count} subjects persisted; status refreshes automatically.`}</p>
        {pollWarning && <p role="status" className="mt-3 border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950">Automatic refresh was interrupted: {pollWarning} Retrying shortly.</p>}
        {actionError && <p role="alert" className="mt-3 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">{actionError}</p>}
        {run.status === "pending" && <button type="button" onClick={execute} disabled={actionBusy !== null} className={`mt-5 ${primaryButtonClass}`}>{actionBusy === "execute" ? "Submitting…" : "Submit judge run"}</button>}
      </header>

      <section aria-labelledby="progress-title" className="mt-7"><h2 id="progress-title" className="text-lg font-semibold">Progress and outcomes</h2><div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7"><Metric label="Subjects" value={progress.subject_count} /><Metric label="Persisted" value={progress.result_count} /><Metric label="Judge passed" value={progress.passed_count} /><Metric label="Judge failed" value={progress.failed_count} /><Metric label="Provider failures" value={progress.error_count} /><Metric label="Pending" value={progress.pending_count} /><Metric label="Attempts" value={run.attempt_count} /></div></section>

      <div className="mt-7 grid gap-6 lg:grid-cols-2">
        <section aria-labelledby="population-title" className="border bg-surface p-5"><h2 id="population-title" className="font-semibold">Human-labelled population</h2><p className="mt-1 text-sm text-muted">Immutable study snapshot used by this run.</p><dl className="mt-4 grid gap-3 sm:grid-cols-2"><Detail label="Study" value={study.name} /><Detail label="Reference set" value={referenceSet.name} /><Detail label="Subjects" value={study.subject_count} /><Detail label="Reference set frozen" value={formatTimestamp(referenceSet.frozen_at)} /></dl></section>
        <section aria-labelledby="snapshot-title" className="border bg-surface p-5"><h2 id="snapshot-title" className="font-semibold">Judge snapshot</h2><p className="mt-1 text-sm text-muted">Configuration stored with the run; secrets are not part of this snapshot.</p><dl className="mt-4 grid gap-3 sm:grid-cols-2"><Detail label="Provider / model" value={`${run.provider} / ${run.model}`} /><Detail label="Configuration ID" value={run.configuration_id} /><Detail label="Timeout" value={`${run.timeout_seconds}s`} /><Detail label="Output-token limit" value={run.max_output_tokens} /></dl><div className="mt-4"><p className="text-xs font-semibold text-muted">Rubric / instructions</p><p className="mt-1 whitespace-pre-wrap text-sm leading-6">{run.rubric}</p></div></section>
      </div>

      <section aria-labelledby="failures-title" className="mt-8 border bg-surface p-5"><div className="flex items-end justify-between gap-3"><div><h2 id="failures-title" className="font-semibold">Provider and execution failures</h2><p className="mt-1 text-sm leading-6 text-muted">Operational failures are not judge decisions and never appear as calibration disagreements.</p></div><span className="font-mono text-sm font-semibold">{progress.error_count}</span></div>{providerFailures.length === 0 ? <p className="mt-4 text-sm text-muted">No provider failures have been persisted.</p> : <ol className="mt-4 divide-y border">{providerFailures.map((item) => <li key={item.trace_id} className="grid gap-2 p-3 sm:grid-cols-[minmax(12rem,1fr)_auto]"><div className="min-w-0"><Link href={`/traces/${encodeURIComponent(item.trace_id)}`} className="font-semibold text-accent hover:underline">Inspect trace</Link><p className="mt-1 truncate font-mono text-xs text-muted">{item.trace_id}</p></div><span className="border border-amber-300 bg-amber-50 px-2 py-1 text-xs font-semibold text-amber-950">{item.error_category?.replaceAll("_", " ")}</span></li>)}</ol>}</section>

      {run.status === "completed" && analysisMissing && <section className="mt-8 border bg-surface p-5"><h2 className="font-semibold">Statistical calibration report</h2><p className="mt-1 text-sm leading-6 text-muted">Create the canonical server-side analysis for this complete population. Statistics are persisted once and not recomputed in the browser.</p><button type="button" onClick={generateAnalysis} disabled={actionBusy !== null} className={`mt-4 ${primaryButtonClass}`}>{actionBusy === "analysis" ? "Creating…" : "Create calibration analysis"}</button></section>}
      {analysis && <><CalibrationReport runId={run.id} analysis={analysis} results={results} category={category} offset={offset} /><RunComparison current={{ run, analysis, study, referenceSet }} /></>}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string | number }) { return <div><dt className="text-xs font-semibold text-muted">{label}</dt><dd className="mt-1 break-all text-sm">{value}</dd></div>; }
