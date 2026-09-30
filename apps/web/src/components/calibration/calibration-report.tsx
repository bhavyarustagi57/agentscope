"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  listDisagreements,
  type CalibrationAnalysis,
  type CalibrationDisagreement,
  type DisagreementCategory,
  type JudgeResult,
} from "@/lib/calibration-api";
import {
  buildDisagreementHref,
  buildTraceHref,
  confusionMatrixCells,
  disagreementDefinition,
  formatMetric,
} from "@/lib/calibration-ui";

import { Metric, ViewState, buttonClass } from "./shared";

export function CalibrationReport({ runId, analysis, results, category, offset }: {
  runId: string; analysis: CalibrationAnalysis; results: JudgeResult[];
  category: DisagreementCategory | undefined; offset: number;
}) {
  const resultByTrace = useMemo(() => new Map(results.map((item) => [item.trace_id, item])), [results]);
  return (
    <section aria-labelledby="report-title" className="mt-10 border-t pt-7">
      <div><p className="font-mono text-xs font-semibold text-accent">CANONICAL ANALYSIS · SCHEMA {analysis.metric_schema_version}</p><h2 id="report-title" className="mt-2 text-2xl font-semibold">Calibration report</h2><p className="mt-1 max-w-3xl text-sm leading-6 text-muted">Human authoritative labels are the reference truth for this report; <strong>passed</strong> is the positive class. Metrics are server-computed evidence, not a trust score or deployment recommendation.</p></div>

      <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Metric label="Sample" value={analysis.sample_count} /><Metric label="Agreements" value={analysis.agreement_count} /><Metric label="Disagreements" value={analysis.disagreement_count} /><Metric label="Human passed" value={analysis.human_passed_count} /><Metric label="Human failed" value={analysis.human_failed_count} /><Metric label="Observed agreement" value={formatMetric(analysis.observed_agreement)} />
      </div>

      <div className="mt-7 grid gap-6 xl:grid-cols-[minmax(24rem,0.9fr)_minmax(30rem,1.1fr)]">
        <section aria-labelledby="metrics-title" className="border bg-surface p-5">
          <h3 id="metrics-title" className="font-semibold">Agreement metrics</h3>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <Metric label="Cohen’s kappa" value={analysis.cohens_kappa === null ? "Undefined" : analysis.cohens_kappa.toFixed(3)} detail="Agreement corrected for agreement expected by chance." />
            <Metric label="Expected agreement" value={formatMetric(analysis.expected_agreement)} detail="Chance agreement implied by the two label marginals." />
            <Metric label="Precision · passed" value={formatMetric(analysis.precision_passed)} detail="Of judge-passed examples, the share humans marked passed." />
            <Metric label="Recall · passed" value={formatMetric(analysis.recall_passed)} detail="Of human-passed examples, the share the judge marked passed." />
            <Metric label="F1 · passed" value={formatMetric(analysis.f1_passed)} detail="Harmonic mean of passed-class precision and recall." />
            <Metric label="Specificity · failed" value={formatMetric(analysis.specificity_failed)} detail="Of human-failed examples, the share the judge correctly marked failed." />
          </div>
          {analysis.undefined_metrics.length > 0 && <p className="mt-4 border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950">Undefined because the required denominator is zero: {analysis.undefined_metrics.map((item) => item.replaceAll("_", " ")).join(", ")}.</p>}
        </section>

        <section aria-labelledby="matrix-title" className="min-w-0 border bg-surface p-5">
          <h3 id="matrix-title" className="font-semibold">Confusion matrix</h3><p className="mt-1 text-sm text-muted">Rows: human authoritative label · Columns: judge decision.</p>
          <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[28rem] border-collapse text-sm"><caption className="sr-only">Confusion matrix with human labels as rows and judge decisions as columns</caption><thead><tr><th scope="col" className="border bg-canvas p-3 text-left">Human ↓ / Judge →</th><th scope="col" className="border bg-canvas p-3 text-center">Passed</th><th scope="col" className="border bg-canvas p-3 text-center">Failed</th></tr></thead><tbody><tr><th scope="row" className="border p-3 text-left">Passed</th><MatrixCell cell={confusionMatrixCells(analysis)[0]} /><MatrixCell cell={confusionMatrixCells(analysis)[1]} /></tr><tr><th scope="row" className="border p-3 text-left">Failed</th><MatrixCell cell={confusionMatrixCells(analysis)[2]} /><MatrixCell cell={confusionMatrixCells(analysis)[3]} /></tr></tbody></table></div>
          <dl className="mt-4 grid gap-2 text-xs text-muted sm:grid-cols-2"><div><dt className="font-semibold text-ink">False positive</dt><dd className="mt-1">Judge passed, human failed.</dd></div><div><dt className="font-semibold text-ink">False negative</dt><dd className="mt-1">Judge failed, human passed.</dd></div></dl>
        </section>
      </div>

      <DisagreementExplorer runId={runId} expectedCount={analysis.disagreement_count} resultByTrace={resultByTrace} category={category} offset={offset} />
    </section>
  );
}

function MatrixCell({ cell }: { cell: ReturnType<typeof confusionMatrixCells>[number] }) {
  return <td className="border p-3 text-center"><span className="block font-mono text-2xl font-semibold">{cell.value}</span><span className="mt-1 block text-xs text-muted">{cell.label}</span></td>;
}

function DisagreementExplorer({ runId, expectedCount, resultByTrace, category, offset }: {
  runId: string; expectedCount: number; resultByTrace: Map<string, JudgeResult>;
  category: DisagreementCategory | undefined; offset: number;
}) {
  const [items, setItems] = useState<CalibrationDisagreement[]>([]); const [hasMore, setHasMore] = useState(false);
  const [loadedKey, setLoadedKey] = useState<string | null>(null); const [error, setError] = useState<string | null>(null); const [retry, setRetry] = useState(0);
  const queryKey = `${category ?? "all"}:${offset}:${retry}`;
  useEffect(() => {
    const controller = new AbortController();
    listDisagreements(runId, { category, offset, pageSize: 20, signal: controller.signal })
      .then((page) => { setItems(page.items); setHasMore(page.has_more); setError(null); setLoadedKey(queryKey); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) { setError(reason instanceof Error ? reason.message : "Unable to load disagreements."); setLoadedKey(queryKey); } });
    return () => controller.abort();
  }, [runId, category, offset, retry, queryKey]);
  const loading = loadedKey !== queryKey;
  return (
    <section aria-labelledby="disagreements-title" className="mt-8 border bg-surface p-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between"><div><h3 id="disagreements-title" className="font-semibold">Disagreement explorer</h3><p className="mt-1 text-sm leading-6 text-muted">Only valid opposing decisions appear here. Provider failures are reported separately and never counted as human/judge disagreement.</p></div><span className="font-mono text-xs text-muted">{expectedCount} TOTAL</span></div>
      <nav aria-label="Disagreement category filter" className="mt-4 flex flex-wrap gap-2">{[[undefined, "All"], ["false_positive", "False positives"], ["false_negative", "False negatives"]].map(([value, label]) => <Link key={label} href={buildDisagreementHref(runId, value as DisagreementCategory | undefined, 0)} aria-current={category === value || (!category && !value) ? "page" : undefined} className={`min-h-11 border px-3 py-3 text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-accent ${category === value || (!category && !value) ? "bg-panel text-white" : "bg-white"}`}>{label}</Link>)}</nav>
      {loading ? <div aria-busy="true" aria-label="Loading disagreements" className="mt-4 h-40 animate-pulse border bg-canvas" /> : error ? <div className="mt-4"><ViewState title="Couldn’t load disagreements" detail={error} role="alert"><button className={`mt-4 ${buttonClass}`} onClick={() => setRetry((value) => value + 1)}>Retry</button></ViewState></div> : items.length === 0 ? <div className="mt-4"><ViewState title="No disagreements" detail={category ? `No ${category.replaceAll("_", " ")} disagreements match this page.` : "Human and judge decisions agree for every analyzed subject."} /></div> : <ol className="mt-4 divide-y border" aria-label="Calibration disagreements">{items.map((item) => { const result = resultByTrace.get(item.trace_id); return <li key={item.trace_id} className="grid gap-3 p-4 lg:grid-cols-[minmax(13rem,1fr)_minmax(14rem,1fr)_auto] lg:items-center"><div className="min-w-0"><Link href={buildTraceHref(item.trace_id)} className="font-semibold text-accent underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-accent">Inspect trace</Link><p className="mt-1 truncate font-mono text-xs text-muted" title={item.trace_id}>{item.trace_id}</p></div><div><p className="text-sm"><strong className="capitalize">{item.category.replaceAll("_", " ")}</strong> · {disagreementDefinition(item.category)}</p><p className="mt-1 text-sm text-muted">Human: <strong className="capitalize text-ink">{item.human_label}</strong> · Judge: <strong className="capitalize text-ink">{item.judge_decision}</strong></p>{result?.rationale && <p className="mt-2 text-sm leading-6 text-muted"><span className="font-semibold text-ink">Judge rationale:</span> {result.rationale}</p>}</div><span className="border bg-canvas px-2 py-1 text-xs font-semibold capitalize">{item.category.replaceAll("_", " ")}</span></li>; })}</ol>}
      {!loading && !error && (offset > 0 || hasMore) && <nav aria-label="Disagreement pages" className="mt-4 flex justify-between gap-3">{offset > 0 ? <Link href={buildDisagreementHref(runId, category, Math.max(0, offset - 20))} className={buttonClass}>← Previous</Link> : <span />}{hasMore && <Link href={buildDisagreementHref(runId, category, offset + 20)} className={buttonClass}>Next →</Link>}</nav>}
    </section>
  );
}
