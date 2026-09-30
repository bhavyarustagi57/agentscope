import type { JsonValue, SpanDetail } from "@/lib/trace-api";
import { formatDuration, formatTimestamp } from "@/lib/trace-explorer";

import { JsonView } from "./json-view";
import { StatusBadge } from "./status-badge";

export function SpanInspector({ span }: { span: SpanDetail | null }) {
  if (!span) {
    return <div className="border bg-surface p-8 text-center text-sm text-muted">Select a span to inspect its captured data.</div>;
  }
  return (
    <aside aria-labelledby="inspector-title" className="min-w-0 border bg-surface">
      <header className="border-b p-4">
        <p className="font-mono text-xs font-semibold uppercase tracking-wide text-accent">Selected span</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <h2 id="inspector-title" className="min-w-0 break-words text-xl font-semibold">{span.name}</h2>
          <StatusBadge status={span.status} />
        </div>
        <p className="mt-2 font-mono text-xs uppercase text-muted">{span.kind}</p>
      </header>

      <div className="space-y-5 p-4">
        <Section title="General">
          <dl className="grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
            <Field label="Span ID" value={span.span_id} mono />
            <Field label="Parent span" value={span.parent_span_id ?? "Root"} mono />
            <Field label="Started · local" value={formatTimestamp(span.started_at)} title={span.started_at} />
            <Field label="Ended · local" value={formatTimestamp(span.ended_at)} title={span.ended_at ?? undefined} />
            <Field label="Duration" value={formatDuration(span.duration_ms)} mono />
          </dl>
        </Section>

        {span.error && (
          <Section title="Error">
            <div className="border border-red-200 bg-red-50 p-3 text-sm text-red-950">
              <p className="font-mono text-xs font-semibold uppercase">{span.error.type}</p>
              <p className="mt-2 whitespace-pre-wrap break-words">{span.error.message}</p>
            </div>
          </Section>
        )}

        {span.llm && (
          <Section title="LLM call">
            <dl className="grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
              <Field label="Provider" value={span.llm.provider ?? "—"} />
              <Field label="Model" value={span.llm.model ?? "—"} />
              <Field label="Operation" value={span.llm.operation ?? "—"} />
              <Field label="Finish reason" value={span.llm.finish_reason ?? "—"} />
              <Field label="Temperature" value={span.llm.temperature?.toString() ?? "—"} mono />
              <Field label="Tokens" value={formatTokens(span)} mono />
            </dl>
            {span.llm.tool_calls.length > 0 && (
              <div className="mt-4 space-y-3">
                <h4 className="text-sm font-semibold">Tool calls</h4>
                {span.llm.tool_calls.map((call, index) => (
                  <div key={`${call.id ?? call.name}-${index}`} className="border p-3">
                    <p className="text-sm font-semibold">{call.name}</p>
                    {call.id && <p className="mt-1 break-all font-mono text-xs text-muted">{call.id}</p>}
                    <div className="mt-3 grid gap-3">
                      <JsonBlock label="Arguments" value={call.arguments} />
                      <JsonBlock label="Result" value={call.result} />
                    </div>
                  </div>
                ))}
              </div>
            )}
            {Object.keys(span.llm.attributes).length > 0 && <div className="mt-4"><JsonBlock label="LLM attributes" value={span.llm.attributes} /></div>}
          </Section>
        )}

        {span.kind === "tool" && (
          <Section title="Tool execution">
            <p className="mb-3 text-xs leading-5 text-muted">Tool details are represented by the captured input, output, metadata, and attributes in schema version 1.</p>
            <div className="grid gap-3"><JsonBlock label="Tool input" value={span.input} /><JsonBlock label="Tool output" value={span.output} /></div>
          </Section>
        )}

        {span.kind !== "tool" && <Section title="Captured values"><div className="grid gap-3"><JsonBlock label="Input" value={span.input} /><JsonBlock label="Output" value={span.output} /></div></Section>}

        {span.events.length > 0 && (
          <Section title={`Events (${span.events.length})`}>
            <ol className="space-y-3">
              {span.events.map((event, index) => (
                <li key={`${event.timestamp}-${event.name}-${index}`} className="border p-3">
                  <p className="text-sm font-semibold">{event.name}</p>
                  <time dateTime={event.timestamp} className="mt-1 block text-xs text-muted" title={event.timestamp}>{formatTimestamp(event.timestamp)}</time>
                  {Object.keys(event.attributes).length > 0 && <div className="mt-3"><JsonView label={`${event.name} attributes`} value={event.attributes} /></div>}
                </li>
              ))}
            </ol>
          </Section>
        )}

        <Section title="Attributes and metadata">
          <div className="grid gap-3"><JsonBlock label="Attributes" value={span.attributes} /><JsonBlock label="Metadata" value={span.metadata} /></div>
        </Section>
      </div>
    </aside>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section><h3 className="mb-3 border-b pb-2 text-sm font-semibold uppercase tracking-wide text-muted">{title}</h3>{children}</section>;
}
function Field({ label, value, mono = false, title }: { label: string; value: string; mono?: boolean; title?: string }) {
  return <div className="min-w-0"><dt className="text-xs text-muted">{label}</dt><dd className={`mt-1 break-words ${mono ? "font-mono text-xs" : ""}`} title={title}>{value}</dd></div>;
}
function JsonBlock({ label, value }: { label: string; value: JsonValue }) {
  return <div><h4 className="mb-1.5 text-xs font-semibold text-muted">{label}</h4><JsonView label={label} value={value} /></div>;
}
function formatTokens(span: SpanDetail): string {
  const usage = span.llm?.token_usage;
  if (!usage) return "—";
  return `${usage.total_tokens ?? "—"} total · ${usage.input_tokens ?? "—"} in · ${usage.output_tokens ?? "—"} out`;
}
