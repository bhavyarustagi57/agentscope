"use client";

import { useEffect, useRef, useState } from "react";

import { executeEvaluationRun, type EvaluationExecutionAccepted } from "@/lib/evaluation-api";
import { listTraces, type TraceSummary } from "@/lib/trace-api";
import { formatDuration, formatTimestamp } from "@/lib/trace-explorer";
import { toggleTraceSelection } from "@/lib/evaluation-ui";

export function TraceSelector({ runId, onSubmitted }: { runId: string; onSubmitted: (response: EvaluationExecutionAccepted) => void }) {
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const loadMoreController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    listTraces({}, { signal: controller.signal })
      .then((response) => {
        setTraces(response.items);
        setNextCursor(response.next_cursor);
        setHasMore(response.has_more);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load traces.");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [retry]);

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    const controller = new AbortController();
    loadMoreController.current = controller;
    setLoadingMore(true);
    try {
      const response = await listTraces({}, { cursor: nextCursor, signal: controller.signal });
      setTraces((current) => [...current, ...response.items.filter((trace) => !current.some((item) => item.trace_id === trace.trace_id))]);
      setNextCursor(response.next_cursor);
      setHasMore(response.has_more);
      setError(null);
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load more traces.");
    } finally {
      if (!controller.signal.aborted) setLoadingMore(false);
    }
  }

  async function submit() {
    if (selected.length === 0 || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      onSubmitted(await executeEvaluationRun(runId, selected));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to submit this run.");
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby="trace-selection-title" className="mt-7 border-t pt-7">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div><h2 id="trace-selection-title" className="text-lg font-semibold">Select traces</h2><p className="mt-1 max-w-2xl text-sm leading-6 text-muted">Choose existing traces for this run. Selection shows identity and availability only; trace output is not exposed here.</p></div>
        <p role="status" className="font-mono text-xs text-muted">{selected.length} selected · 1,000 maximum</p>
      </div>

      {error && <div role="alert" className="mt-4 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">{error} {traces.length === 0 && <button onClick={() => { setLoading(true); setRetry((value) => value + 1); }} className="ml-2 underline focus-visible:outline-2 focus-visible:outline-accent">Retry</button>}</div>}
      {loading ? <div aria-busy="true" aria-label="Loading selectable traces" className="mt-4 h-64 animate-pulse border bg-surface" /> : traces.length === 0 ? <div role="status" className="mt-4 border bg-surface px-6 py-10 text-center"><h3 className="font-semibold">No traces available</h3><p className="mt-2 text-sm text-muted">Ingest traces before submitting this run.</p></div> : (
        <fieldset className="mt-4 border bg-surface">
          <legend className="sr-only">Traces for evaluation</legend>
          <ul className="divide-y">
            {traces.map((trace) => {
              const checked = selected.includes(trace.trace_id);
              return <li key={trace.trace_id}><label className="grid cursor-pointer gap-3 p-4 hover:bg-canvas/70 sm:grid-cols-[auto_minmax(12rem,1fr)_minmax(14rem,0.8fr)] sm:items-center"><input type="checkbox" checked={checked} onChange={() => setSelected((current) => toggleTraceSelection(current, trace.trace_id))} className="h-5 w-5 accent-accent" /><span className="min-w-0"><span className="block truncate font-semibold">{trace.name}</span><span className="mt-1 block truncate font-mono text-xs text-muted">{trace.trace_id}</span></span><span className="grid grid-cols-2 gap-2 text-xs text-muted"><span><strong className="block text-ink">{trace.output_available ? "Output available" : "Output unavailable"}</strong>{formatTimestamp(trace.started_at)}</span><span><strong className="block text-ink">Duration</strong>{formatDuration(trace.duration_ms)}</span></span></label></li>;
            })}
          </ul>
        </fieldset>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button onClick={submit} disabled={selected.length === 0 || submitting} className="min-h-11 bg-panel px-5 text-sm font-semibold text-white outline-none hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2">{submitting ? "Submitting…" : `Execute ${selected.length || ""} trace${selected.length === 1 ? "" : "s"}`}</button>
        {hasMore && <button onClick={loadMore} disabled={loadingMore} className="min-h-11 border bg-white px-5 text-sm font-semibold outline-none hover:bg-canvas disabled:cursor-wait disabled:opacity-60 focus-visible:ring-2 focus-visible:ring-accent">{loadingMore ? "Loading…" : "Load more traces"}</button>}
      </div>
    </section>
  );
}
