import type { EvaluationOutcome, EvaluationRunStatus } from "@/lib/evaluation-api";
import { outcomeLabel } from "@/lib/evaluation-ui";

const runStyles: Record<EvaluationRunStatus, string> = {
  pending: "border-slate-300 bg-slate-50 text-slate-700",
  queued: "border-amber-300 bg-amber-50 text-amber-900",
  running: "border-blue-300 bg-blue-50 text-blue-900",
  completed: "border-emerald-300 bg-emerald-50 text-emerald-900",
  failed: "border-red-300 bg-red-50 text-red-900",
};

const outcomeStyles: Record<EvaluationOutcome, string> = {
  passed: "border-emerald-300 bg-emerald-50 text-emerald-900",
  failed: "border-red-300 bg-red-50 text-red-900",
  error: "border-amber-300 bg-amber-50 text-amber-900",
};

export function RunStatusBadge({ status }: { status: EvaluationRunStatus }) {
  return (
    <span className={`inline-flex border px-2 py-1 text-xs font-semibold capitalize ${runStyles[status]}`}>
      {status}
    </span>
  );
}

export function OutcomeBadge({ outcome }: { outcome: EvaluationOutcome }) {
  return (
    <span className={`inline-flex border px-2 py-1 text-xs font-semibold ${outcomeStyles[outcome]}`}>
      {outcomeLabel(outcome)}
    </span>
  );
}
