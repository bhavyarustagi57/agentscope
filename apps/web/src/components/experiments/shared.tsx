import type { ExperimentRunStatus, ExperimentStatus } from "@/lib/experiment-api";

const baseButtonClass = "inline-flex min-h-11 items-center justify-center border px-4 text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-60";
export const buttonClass = `${baseButtonClass} bg-white hover:bg-slate-50`;
export const primaryButtonClass = `${baseButtonClass} border-accent bg-accent text-white hover:bg-emerald-800`;
export const inputClass = "min-h-11 w-full border bg-white px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-accent";

const statusStyles: Record<ExperimentStatus | ExperimentRunStatus, string> = {
  draft: "border-slate-300 bg-slate-50 text-slate-700", pending: "border-slate-300 bg-slate-50 text-slate-700",
  ready: "border-cyan-300 bg-cyan-50 text-cyan-950", queued: "border-amber-300 bg-amber-50 text-amber-950",
  running: "border-blue-300 bg-blue-50 text-blue-950", completed: "border-emerald-300 bg-emerald-50 text-emerald-950",
  failed: "border-red-300 bg-red-50 text-red-900",
};

export function StatusBadge({ status }: { status: ExperimentStatus | ExperimentRunStatus }) {
  return <span className={`inline-flex border px-2 py-1 text-xs font-semibold uppercase tracking-wide ${statusStyles[status]}`}>{status}</span>;
}
export function ViewState({ title, detail, role = "status", headingLevel = "h2", children }: { title: string; detail: string; role?: "status" | "alert"; headingLevel?: "h1" | "h2" | "h3"; children?: React.ReactNode }) {
  const Heading = headingLevel;
  return <div role={role} className="border bg-surface px-6 py-10 text-center"><Heading className="font-semibold">{title}</Heading><p className="mx-auto mt-2 max-w-2xl text-sm leading-6 text-muted">{detail}</p>{children}</div>;
}
export function Metric({ label, value, detail }: { label: string; value: string | number; detail?: string }) {
  return <dl className="border bg-white px-3 py-3"><dt className="text-xs font-semibold text-muted">{label}</dt><dd className="mt-1 font-mono text-lg font-semibold">{value}</dd>{detail && <dd className="mt-1 text-xs leading-5 text-muted">{detail}</dd>}</dl>;
}
