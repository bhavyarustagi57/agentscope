import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";

import type { JudgeRunStatus, ReferenceSetStatus } from "@/lib/calibration-api";

export const inputClass = "min-h-11 w-full border bg-white px-3 font-normal outline-none focus-visible:ring-2 focus-visible:ring-accent";
export const buttonClass = "min-h-11 border bg-white px-4 text-sm font-semibold outline-none hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-accent";
export const primaryButtonClass = "min-h-11 bg-panel px-4 text-sm font-semibold text-white outline-none hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2";

const referenceStyles: Record<ReferenceSetStatus, string> = {
  draft: "border-slate-300 bg-slate-50 text-slate-700",
  labeling: "border-blue-300 bg-blue-50 text-blue-900",
  frozen: "border-emerald-300 bg-emerald-50 text-emerald-900",
};
const runStyles: Record<JudgeRunStatus, string> = {
  pending: "border-slate-300 bg-slate-50 text-slate-700",
  queued: "border-amber-300 bg-amber-50 text-amber-900",
  running: "border-blue-300 bg-blue-50 text-blue-900",
  completed: "border-emerald-300 bg-emerald-50 text-emerald-900",
  failed: "border-red-300 bg-red-50 text-red-900",
};

export function ReferenceStatusBadge({ status }: { status: ReferenceSetStatus }) {
  return <span className={`inline-flex border px-2 py-1 text-xs font-semibold capitalize ${referenceStyles[status]}`}>{status}</span>;
}

export function JudgeRunStatusBadge({ status }: { status: JudgeRunStatus }) {
  return <span className={`inline-flex border px-2 py-1 text-xs font-semibold capitalize ${runStyles[status]}`}>{status}</span>;
}

export function Field({ label, error, hint, required = false, children }: {
  label: string; error?: string; hint?: string; required?: boolean; children: ReactNode;
}) {
  const id = useId();
  const describedBy = [hint ? `${id}-hint` : "", error ? `${id}-error` : ""].filter(Boolean).join(" ") || undefined;
  const control = isValidElement(children) ? cloneElement(children as ReactElement<{ "aria-describedby"?: string; "aria-invalid"?: boolean }>, { "aria-describedby": describedBy, "aria-invalid": Boolean(error) }) : children;
  return (
    <label className="grid gap-1.5 text-sm font-semibold">
      <span>{label}{required && <span className="text-red-800"> (required)</span>}</span>
      {control}
      {hint && <span id={`${id}-hint`} className="text-xs font-normal leading-5 text-muted">{hint}</span>}
      {error && <span id={`${id}-error`} role="alert" className="text-sm font-normal text-red-800">{error}</span>}
    </label>
  );
}

export function ViewState({ title, detail, role = "status", headingLevel = "h3", children }: {
  title: string; detail: string; role?: "status" | "alert"; headingLevel?: "h1" | "h2" | "h3"; children?: ReactNode;
}) {
  const Heading = headingLevel;
  return (
    <div role={role} className="border bg-surface px-6 py-10 text-center">
      <Heading className="font-semibold">{title}</Heading>
      <p className="mx-auto mt-2 max-w-2xl text-sm leading-6 text-muted">{detail}</p>
      {children}
    </div>
  );
}

export function Metric({ label, value, detail }: { label: string; value: string | number; detail?: string }) {
  return (
    <dl className="border bg-white px-3 py-3">
      <dt className="text-xs font-semibold text-muted">{label}</dt>
      <dd className="mt-1 font-mono text-lg font-semibold">{value}</dd>
      {detail && <dd className="mt-1 text-xs leading-5 text-muted">{detail}</dd>}
    </dl>
  );
}
