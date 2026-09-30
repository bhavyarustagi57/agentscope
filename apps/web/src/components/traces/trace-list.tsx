import Link from "next/link";

import type { TraceSummary } from "@/lib/trace-api";
import { buildTraceDetailHref, formatDuration, formatTimestamp } from "@/lib/trace-explorer";

import { StatusBadge } from "./status-badge";

export function TraceList({ traces, explorerQuery }: { traces: TraceSummary[]; explorerQuery: string }) {
  return (
    <ol aria-label="Traces" className="divide-y border bg-surface">
      {traces.map((trace) => (
        <li key={trace.trace_id}>
          <Link
            href={buildTraceDetailHref(trace.trace_id, explorerQuery)}
            className="group grid gap-4 p-4 outline-none hover:bg-canvas/70 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent lg:grid-cols-[minmax(15rem,1.4fr)_minmax(11rem,0.9fr)_auto] lg:items-center"
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="truncate font-semibold group-hover:text-accent">{trace.name}</h2>
                <StatusBadge status={trace.status} />
              </div>
              <p className="mt-2 truncate font-mono text-xs text-muted" title={trace.trace_id}>
                {trace.trace_id.slice(0, 12)}{trace.trace_id.length > 12 ? "…" : ""}
              </p>
            </div>
            <dl className="grid grid-cols-2 gap-x-5 gap-y-2 text-sm">
              <div>
                <dt className="text-xs text-muted">Started · local</dt>
                <dd className="mt-0.5" title={trace.started_at}>{formatTimestamp(trace.started_at)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">Duration</dt>
                <dd className="mt-0.5 font-mono">{formatDuration(trace.duration_ms)}</dd>
              </div>
            </dl>
            <dl className="grid grid-cols-3 gap-2 text-center text-xs sm:grid-cols-5 lg:min-w-[22rem]">
              <Metric label="Spans" value={trace.span_count} />
              <Metric label="LLM" value={trace.llm_span_count} />
              <Metric label="Tools" value={trace.tool_span_count} />
              <Metric label="Errors" value={trace.error_span_count} alert={trace.error_span_count > 0 || trace.status === "error"} />
              <Metric label="Tokens" value={trace.total_tokens ?? "—"} />
            </dl>
          </Link>
        </li>
      ))}
    </ol>
  );
}

function Metric({ label, value, alert = false }: { label: string; value: number | string; alert?: boolean }) {
  return (
    <div className={`border px-2 py-2 ${alert ? "border-red-200 bg-red-50" : "bg-white"}`}>
      <dt className="text-muted">{label}</dt>
      <dd className={`mt-1 font-mono font-semibold ${alert ? "text-red-800" : "text-ink"}`}>{value}</dd>
    </div>
  );
}
