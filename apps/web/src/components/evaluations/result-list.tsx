import Link from "next/link";

import type { EvaluationOutcome, EvaluationResult } from "@/lib/evaluation-api";
import { buildRunResultsHref, buildTraceHref, formatConfigValue, formatScore, resultEmptyMessage } from "@/lib/evaluation-ui";

import { OutcomeBadge } from "./evaluation-status";

const outcomes: Array<{ value: EvaluationOutcome | undefined; label: string }> = [
  { value: undefined, label: "All" },
  { value: "passed", label: "Passed" },
  { value: "failed", label: "Failed" },
  { value: "error", label: "Errors" },
];

export function ResultList({ runId, results, outcome, offset, hasMore }: { runId: string; results: EvaluationResult[]; outcome?: EvaluationOutcome; offset: number; hasMore: boolean }) {
  return (
    <section aria-labelledby="results-title" className="mt-8 border-t pt-7">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div><h2 id="results-title" className="text-lg font-semibold">Results</h2><p className="mt-1 text-sm text-muted">Bounded pages of 20, newest first for a stable dataset.</p></div>
        <nav aria-label="Filter evaluation results" className="flex flex-wrap gap-2">{outcomes.map((item) => <Link key={item.label} href={buildRunResultsHref(runId, item.value, 0)} aria-current={item.value === outcome ? "page" : undefined} className={`min-h-11 border px-3 py-3 text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-accent ${item.value === outcome ? "bg-panel text-white" : "bg-white hover:bg-canvas"}`}>{item.label}</Link>)}</nav>
      </div>

      {results.length === 0 ? <div role="status" className="mt-4 border bg-surface px-6 py-10 text-center"><h3 className="font-semibold">No results</h3><p className="mt-2 text-sm text-muted">{resultEmptyMessage(outcome)}</p></div> : (
        <ol className="mt-4 space-y-3" aria-label="Evaluation results">
          {results.map((result) => <li key={result.id} className="border bg-surface p-4"><div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><OutcomeBadge outcome={result.outcome} /><span className="font-mono text-xs text-muted">Score: {formatScore(result.score)}</span></div><h3 className="mt-3 truncate font-semibold">{result.trace_name ?? result.trace_id}</h3><p className="mt-1 truncate font-mono text-xs text-muted" title={result.trace_id}>{result.trace_id}</p></div><Link href={buildTraceHref(result.trace_id)} className="min-h-11 shrink-0 border bg-white px-4 py-3 text-sm font-semibold outline-none hover:bg-canvas focus-visible:ring-2 focus-visible:ring-accent">Open trace</Link></div>{Object.keys(result.details).length > 0 && <details className="mt-4 border-t pt-3"><summary className="cursor-pointer text-sm font-semibold text-muted focus-visible:outline-2 focus-visible:outline-accent">Reason details</summary><dl className="mt-3 grid gap-3 sm:grid-cols-2">{Object.entries(result.details).map(([key, value]) => <div key={key} className="min-w-0"><dt className="text-xs font-semibold text-muted">{key.replaceAll("_", " ")}</dt><dd className="mt-1 break-words text-sm">{formatConfigValue(value)}</dd></div>)}</dl></details>}</li>)}
        </ol>
      )}

      <nav aria-label="Result pages" className="mt-4 flex items-center justify-between border bg-surface p-3"><span className="text-xs text-muted">Offset {offset}</span><div className="flex gap-2">{offset > 0 && <Link href={buildRunResultsHref(runId, outcome, Math.max(0, offset - 20))} className="min-h-11 border bg-white px-4 py-3 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-accent">Previous</Link>}{hasMore && <Link href={buildRunResultsHref(runId, outcome, offset + 20)} className="min-h-11 border bg-white px-4 py-3 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-accent">Next</Link>}</div></nav>
    </section>
  );
}
