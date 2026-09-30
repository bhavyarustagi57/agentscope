"use client";

import { useEffect, useId, useRef, useState } from "react";

import { buttonClass, primaryButtonClass } from "@/components/experiments/shared";
import { shortId } from "@/lib/product-ui";

export function CopyableReference({ value, label = "identifier" }: { value: string; label?: string }) {
  const [feedback, setFeedback] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setFeedback("Copied");
    } catch {
      setFeedback("Copy failed");
    }
  }
  return <span className="inline-flex max-w-full flex-wrap items-center gap-2">
    <code className="max-w-full break-all text-xs" title={value}>{shortId(value)}</code>
    <button type="button" onClick={copy} className="min-h-11 border bg-white px-3 text-xs font-semibold focus-visible:ring-2 focus-visible:ring-accent" aria-label={`Copy full ${label}`}>Copy</button>
    {feedback && <span role="status" aria-live="polite" className="text-xs text-muted">{feedback}</span>}
  </span>;
}

export function CopyableCommand({ value }: { value: string }) {
  const [feedback, setFeedback] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setFeedback("Copied");
    } catch {
      setFeedback("Copy failed");
    }
  }
  return <div className="mt-4 flex max-w-full flex-wrap items-center gap-2">
    <code className="min-w-0 flex-1 overflow-x-auto border bg-canvas p-3 text-sm">{value}</code>
    <button type="button" onClick={copy} className="min-h-11 border bg-white px-3 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-accent" aria-label="Copy demo command">Copy command</button>
    {feedback && <span role="status" aria-live="polite" className="text-xs text-muted">{feedback}</span>}
  </div>;
}

export function ConfirmAction({ triggerLabel, confirmLabel, description, busy = false, onConfirm }: {
  triggerLabel: string;
  confirmLabel: string;
  description: string;
  busy?: boolean;
  onConfirm: () => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);
  useEffect(() => {
    if (open) confirmRef.current?.focus();
    else if (restoreFocus.current) { triggerRef.current?.focus(); restoreFocus.current = false; }
  }, [open]);
  function close() { restoreFocus.current = true; setOpen(false); }
  if (!open) return <button ref={triggerRef} type="button" onClick={() => setOpen(true)} disabled={busy} className={primaryButtonClass}>{triggerLabel}</button>;
  return <div role="alertdialog" aria-labelledby={titleId} aria-describedby={`${titleId}-description`} onKeyDown={(event) => { if (event.key === "Escape" && !busy) { event.preventDefault(); close(); } }} className="max-w-xl border border-amber-400 bg-amber-50 p-4 text-amber-950">
    <p id={titleId} className="font-semibold">Confirm irreversible action</p>
    <p id={`${titleId}-description`} className="mt-1 text-sm leading-6">{description}</p>
    <div className="mt-3 flex flex-wrap gap-2">
      <button ref={confirmRef} type="button" disabled={busy} onClick={async () => { await onConfirm(); close(); }} className={primaryButtonClass}>{busy ? "Working…" : confirmLabel}</button>
      <button type="button" disabled={busy} onClick={close} className={buttonClass}>Cancel</button>
    </div>
  </div>;
}
