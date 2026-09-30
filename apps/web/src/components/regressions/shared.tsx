import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";

import { CopyableReference } from "@/components/workflow-controls";
import { analysisOutcomeLabel, classificationLabel } from "@/lib/regression-ui";
import type { BisectionAnalysisStatus, RegressionClassification } from "@/lib/regression-api";

const styles: Record<RegressionClassification | BisectionAnalysisStatus, string> = {
  regression_detected: "border-red-300 bg-red-50 text-red-900",
  no_regression_detected: "border-emerald-300 bg-emerald-50 text-emerald-950",
  insufficient_evidence: "border-amber-300 bg-amber-50 text-amber-950",
  pending: "border-slate-300 bg-slate-50 text-slate-700",
  queued: "border-amber-300 bg-amber-50 text-amber-950",
  running: "border-blue-300 bg-blue-50 text-blue-950",
  waiting: "border-cyan-300 bg-cyan-50 text-cyan-950",
  attributed: "border-emerald-300 bg-emerald-50 text-emerald-950",
  inconclusive: "border-amber-300 bg-amber-50 text-amber-950",
  failed: "border-red-300 bg-red-50 text-red-900",
};

export function EvidenceBadge({ value }: { value: RegressionClassification | BisectionAnalysisStatus }) {
  const label = value.includes("evidence") || value.includes("regression_") ? classificationLabel(value as RegressionClassification) : analysisOutcomeLabel(value as BisectionAnalysisStatus);
  return <span className={`inline-flex border px-2 py-1 text-xs font-semibold uppercase tracking-wide ${styles[value]}`}>{label}</span>;
}

export function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string; children: ReactNode }) {
  const id = useId();
  const describedBy = [hint ? `${id}-hint` : "", error ? `${id}-error` : ""].filter(Boolean).join(" ") || undefined;
  const control = isValidElement(children) ? cloneElement(children as ReactElement<{ "aria-describedby"?: string; "aria-invalid"?: boolean }>, { "aria-describedby": describedBy, "aria-invalid": Boolean(error) }) : children;
  return <label className="grid min-w-0 gap-1 text-sm font-semibold"><span>{label}</span>{hint && <span id={`${id}-hint`} className="font-normal leading-5 text-muted">{hint}</span>}{control}{error && <span id={`${id}-error`} role="alert" className="font-normal text-red-800">{error}</span>}</label>;
}

export function FullSha({ value }: { value: string }) {
  return <CopyableReference value={value} label="Git SHA" />;
}
