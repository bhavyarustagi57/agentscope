"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { CopyableReference } from "@/components/workflow-controls";
import { getTrace, TraceApiError, type SpanDetail, type TraceDetail as TraceDetailData } from "@/lib/trace-api";
import { buildSpanTree, formatDuration, formatTimestamp } from "@/lib/trace-explorer";

import { JsonView } from "./json-view";
import { SpanInspector } from "./span-inspector";
import { SpanTree } from "./span-tree";
import { StatusBadge } from "./status-badge";

export function TraceDetail({ traceId, explorerHref }: { traceId: string; explorerHref: string }) {
  const [trace, setTrace] = useState<TraceDetailData | null>(null);
  const [selected, setSelected] = useState<SpanDetail | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ message: string; notFound: boolean } | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getTrace(traceId, controller.signal)
      .then((response) => {
        const roots = buildSpanTree(response.spans);
        setTrace(response);
        setSelected(response.spans[0] ?? null);
        setExpanded(new Set(roots.map((node) => node.span.span_id)));
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError({
            message: reason instanceof Error ? reason.message : "Unable to load this trace.",
            notFound: reason instanceof TraceApiError && reason.status === 404,
          });
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [traceId, retry]);

  const roots = useMemo(() => buildSpanTree(trace?.spans ?? []), [trace]);

  if (loading) return <DetailSkeleton />;
  if (error) return (
    <State title={error.notFound ? "Trace not found" : "Couldn’t load trace"} detail={error.notFound ? "This trace ID does not exist or is no longer available." : error.message}>
      <div className="mt-5 flex flex-wrap justify-center gap-3">
        <Link href={explorerHref} className="min-h-11 border bg-white px-4 py-3 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-accent">Back to traces</Link>
        {!error.notFound && <button onClick={() => { setLoading(true); setError(null); setRetry((value) => value + 1); }} className="min-h-11 bg-panel px-4 text-sm font-semibold text-white focus-visible:ring-2 focus-visible:ring-accent">Retry</button>}
      </div>
    </State>
  );
  if (!trace) return null;

  const errorSpans = trace.spans.filter((span) => span.status === "error").length;
  const llmSpans = trace.spans.filter((span) => span.kind === "llm");
  const toolSpans = trace.spans.filter((span) => span.kind === "tool").length;
  const knownTokenTotals = llmSpans.flatMap((span) => {
    const total = span.llm?.token_usage?.total_tokens;
    return total === null || total === undefined ? [] : [total];
  });
  const tokens = knownTokenTotals.length > 0
    ? knownTokenTotals.reduce((total, value) => total + value, 0)
    : null;

  return (
    <div className="mx-auto max-w-[100rem]">
      <Link href={explorerHref} className="inline-flex min-h-11 items-center text-sm font-semibold text-accent underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-accent">← Back to Trace Explorer</Link>
      <header className="mt-3 border bg-surface p-5 sm:p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <p className="font-mono text-xs font-semibold tracking-[0.16em] text-accent">TRACE DETAIL</p>
            <div className="mt-2 flex flex-wrap items-center gap-3"><h1 className="break-words text-2xl font-semibold tracking-tight sm:text-3xl">{trace.name}</h1><StatusBadge status={trace.status} /></div>
            <div className="mt-3 text-muted"><CopyableReference value={trace.trace_id} label="trace ID" /></div>
            {trace.tags.length > 0 && <div className="mt-3 flex flex-wrap gap-2" aria-label="Trace tags">{trace.tags.map((tag) => <span key={tag} className="border bg-canvas px-2 py-1 text-xs">{tag}</span>)}</div>}
          </div>
          <dl className="grid shrink-0 grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:max-w-2xl">
            <Metric label="Duration" value={formatDuration(trace.duration_ms)} />
            <Metric label="Spans" value={trace.spans.length} />
            <Metric label="Errors" value={errorSpans} alert={errorSpans > 0 || trace.status === "error"} />
            <Metric label="LLM" value={llmSpans.length} />
            <Metric label="Tools" value={toolSpans} />
            <Metric label="Tokens" value={tokens ?? "—"} />
          </dl>
        </div>
        <dl className="mt-5 grid gap-3 border-t pt-4 text-sm sm:grid-cols-3">
          <Time label="Started · local" value={trace.started_at} />
          <Time label="Ended · local" value={trace.ended_at} />
          <Time label="Ingested · local" value={trace.ingested_at} />
        </dl>
      </header>

      <div className="mt-5 grid min-w-0 gap-5 xl:grid-cols-[minmax(25rem,1.1fr)_minmax(25rem,0.9fr)] xl:items-start">
        <SpanTree
          roots={roots}
          expanded={expanded}
          selectedId={selected?.span_id ?? null}
          onSelect={setSelected}
          onToggle={(spanId) => setExpanded((current) => {
            const next = new Set(current);
            if (next.has(spanId)) next.delete(spanId); else next.add(spanId);
            return next;
          })}
        />
        <SpanInspector span={selected} />
      </div>

      <details className="mt-5 border bg-surface p-4">
        <summary className="cursor-pointer font-semibold focus-visible:outline-2 focus-visible:outline-accent">Trace-level captured data</summary>
        <div className="mt-4 grid min-w-0 gap-4 lg:grid-cols-3">
          <JsonSection label="Trace input" value={trace.input} />
          <JsonSection label="Trace output" value={trace.output} />
          <JsonSection label="Trace metadata" value={trace.metadata} />
        </div>
        {trace.error && <div className="mt-4 border border-red-200 bg-red-50 p-3 text-sm text-red-950"><strong>{trace.error.type}:</strong> {trace.error.message}</div>}
      </details>
    </div>
  );
}

function Metric({ label, value, alert = false }: { label: string; value: string | number; alert?: boolean }) {
  return <div><dt className="text-xs text-muted">{label}</dt><dd className={`mt-1 font-mono font-semibold ${alert ? "text-red-800" : ""}`}>{value}</dd></div>;
}
function Time({ label, value }: { label: string; value: string | null }) {
  return <div><dt className="text-xs text-muted">{label}</dt><dd className="mt-1" title={value ?? undefined}>{formatTimestamp(value)}</dd></div>;
}
function JsonSection({ label, value }: { label: string; value: TraceDetailData["input"] }) {
  return <section className="min-w-0"><h2 className="mb-2 text-sm font-semibold">{label}</h2><JsonView label={label} value={value} /></section>;
}
function DetailSkeleton() {
  return <div aria-busy="true" aria-label="Loading trace detail" className="mx-auto max-w-[100rem] space-y-5"><div className="h-56 animate-pulse border bg-surface" /><div className="grid gap-5 xl:grid-cols-2"><div className="h-96 animate-pulse border bg-surface" /><div className="h-96 animate-pulse border bg-surface" /></div></div>;
}
function State({ title, detail, children }: { title: string; detail: string; children: React.ReactNode }) {
  return <div role="status" className="mx-auto mt-16 max-w-xl border bg-surface p-10 text-center"><h1 className="text-2xl font-semibold">{title}</h1><p className="mt-3 text-sm leading-6 text-muted">{detail}</p>{children}</div>;
}
