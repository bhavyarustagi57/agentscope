"use client";

import { useEffect, useState } from "react";

import {
  getCalibrationAnalysis,
  getReferenceSet,
  getStudy,
  listJudgeRuns,
  type CalibrationAnalysis,
  type CalibrationStudy,
  type JudgeRun,
  type ReferenceSet,
} from "@/lib/calibration-api";
import { comparePopulation, formatMetric } from "@/lib/calibration-ui";

import { ViewState, buttonClass, inputClass } from "./shared";

type ComparisonSide = { run: JudgeRun; analysis: CalibrationAnalysis; study: CalibrationStudy; referenceSet: ReferenceSet };

export function RunComparison({ current }: { current: ComparisonSide }) {
  const [eligibleRuns, setEligibleRuns] = useState<JudgeRun[]>([]); const [selectedId, setSelectedId] = useState("");
  const [other, setOther] = useState<ComparisonSide | null>(null); const [loading, setLoading] = useState(true);
  const [comparing, setComparing] = useState(false); const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    listJudgeRuns(controller.signal).then((page) => {
      setEligibleRuns(page.items.filter((item) => item.status === "completed" && item.id !== current.run.id));
      setError(null);
    }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load completed runs."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [current.run.id]);

  async function compare() {
    if (!selectedId) return;
    setComparing(true); setError(null); setOther(null);
    try {
      const run = eligibleRuns.find((item) => item.id === selectedId);
      if (!run) throw new Error("Choose an eligible completed run.");
      const [analysis, study] = await Promise.all([getCalibrationAnalysis(run.id), getStudy(run.study_id)]);
      const referenceSet = await getReferenceSet(study.reference_set_id);
      setOther({ run, analysis, study, referenceSet });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to compare these runs. Generate the other run’s analysis first."); }
    finally { setComparing(false); }
  }

  return (
    <section aria-labelledby="comparison-title" className="mt-10 border-t pt-7">
      <h2 id="comparison-title" className="text-2xl font-semibold">Run comparison</h2>
      <p className="mt-1 max-w-3xl text-sm leading-6 text-muted">Descriptive side-by-side evidence only. A larger metric does not prove statistical superiority, and no winner is declared.</p>
      {loading ? <div aria-busy="true" aria-label="Loading comparable runs" className="mt-4 h-24 animate-pulse border bg-surface" /> : eligibleRuns.length === 0 ? <div className="mt-4"><ViewState title="No other completed runs" detail="Complete and analyze another judge run to compare it here." /></div> : <div className="mt-4 flex flex-col gap-3 border bg-surface p-4 sm:flex-row sm:items-end"><label className="grid flex-1 gap-2 text-sm font-semibold">Compare with completed run<select value={selectedId} onChange={(event) => setSelectedId(event.target.value)} className={inputClass}><option value="">Choose a completed run</option>{eligibleRuns.map((run) => <option key={run.id} value={run.id}>{run.provider} · {run.model} · {run.study_id.slice(0, 8)}</option>)}</select></label><button type="button" onClick={compare} disabled={!selectedId || comparing} className={buttonClass}>{comparing ? "Loading…" : "Compare runs"}</button></div>}
      {error && <p role="alert" className="mt-4 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">{error}</p>}
      {other && <ComparisonTable left={current} right={other} />}
    </section>
  );
}

function ComparisonTable({ left, right }: { left: ComparisonSide; right: ComparisonSide }) {
  const population = comparePopulation(left.run.study_id, right.run.study_id);
  const rows: Array<[string, string | number, string | number]> = [
    ["Provider / model", `${left.run.provider} / ${left.run.model}`, `${right.run.provider} / ${right.run.model}`],
    ["Configuration", left.run.configuration_id, right.run.configuration_id],
    ["Study", left.study.name, right.study.name],
    ["Reference set", left.referenceSet.name, right.referenceSet.name],
    ["Sample size", left.analysis.sample_count, right.analysis.sample_count],
    ["Observed agreement", formatMetric(left.analysis.observed_agreement), formatMetric(right.analysis.observed_agreement)],
    ["Cohen’s kappa", left.analysis.cohens_kappa?.toFixed(3) ?? "Undefined", right.analysis.cohens_kappa?.toFixed(3) ?? "Undefined"],
    ["Precision · passed", formatMetric(left.analysis.precision_passed), formatMetric(right.analysis.precision_passed)],
    ["Recall · passed", formatMetric(left.analysis.recall_passed), formatMetric(right.analysis.recall_passed)],
    ["F1 · passed", formatMetric(left.analysis.f1_passed), formatMetric(right.analysis.f1_passed)],
    ["Specificity · failed", formatMetric(left.analysis.specificity_failed), formatMetric(right.analysis.specificity_failed)],
    ["False positives", left.analysis.false_positive, right.analysis.false_positive],
    ["False negatives", left.analysis.false_negative, right.analysis.false_negative],
    ["Total disagreements", left.analysis.disagreement_count, right.analysis.disagreement_count],
  ];
  return <div className="mt-5"><p role="status" className={`border px-4 py-3 text-sm ${population.sameStudy ? "border-emerald-300 bg-emerald-50 text-emerald-950" : "border-amber-300 bg-amber-50 text-amber-950"}`}>{population.sameStudy ? "Same-study comparison: both runs use the identical immutable subject/reference-label snapshot." : population.warning}</p><div className="mt-4 overflow-x-auto"><table className="w-full min-w-[44rem] border-collapse text-sm"><caption className="sr-only">Side-by-side calibration run comparison</caption><thead><tr><th scope="col" className="border bg-canvas p-3 text-left">Measure</th><th scope="col" className="border bg-canvas p-3 text-left">Current run</th><th scope="col" className="border bg-canvas p-3 text-left">Compared run</th></tr></thead><tbody>{rows.map(([label, leftValue, rightValue]) => <tr key={label}><th scope="row" className="border p-3 text-left font-semibold">{label}</th><td className="break-all border p-3 font-mono">{leftValue}</td><td className="break-all border p-3 font-mono">{rightValue}</td></tr>)}</tbody></table></div></div>;
}
