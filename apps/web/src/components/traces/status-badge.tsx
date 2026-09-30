import type { TraceStatus } from "@/lib/trace-explorer";

const styles: Record<TraceStatus, string> = {
  success: "border-emerald-300 bg-emerald-50 text-emerald-800",
  error: "border-red-300 bg-red-50 text-red-800",
  running: "border-blue-300 bg-blue-50 text-blue-800",
  unset: "border-slate-300 bg-slate-50 text-slate-700",
};

const symbols: Record<TraceStatus, string> = {
  success: "✓",
  error: "!",
  running: "●",
  unset: "–",
};

export function StatusBadge({ status }: { status: TraceStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 border px-2 py-1 text-xs font-semibold uppercase tracking-wide ${styles[status]}`}>
      <span aria-hidden="true">{symbols[status]}</span>
      {status}
    </span>
  );
}
