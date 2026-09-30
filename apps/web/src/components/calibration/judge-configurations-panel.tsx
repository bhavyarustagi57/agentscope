"use client";

import { useState } from "react";

import { CalibrationApiError, createJudgeConfiguration, type JudgeConfiguration } from "@/lib/calibration-api";
import { buildConfigurationPayload } from "@/lib/calibration-ui";
import { formatTimestamp } from "@/lib/trace-explorer";

import { Field, ViewState, inputClass, primaryButtonClass } from "./shared";

const initialDraft = { name: "", description: "", model: "", rubric: "", timeoutSeconds: "30", maxOutputTokens: "300" };

export function JudgeConfigurationsPanel({ configurations, onCreated }: {
  configurations: JudgeConfiguration[]; onCreated: (item: JudgeConfiguration) => void;
}) {
  const [draft, setDraft] = useState(initialDraft); const [errors, setErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null); const [submitting, setSubmitting] = useState(false);
  function update(key: keyof typeof initialDraft, value: string) { setDraft((current) => ({ ...current, [key]: value })); setErrors((current) => ({ ...current, [key]: "" })); }
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); const validated = buildConfigurationPayload(draft);
    if (!validated.payload) { setErrors(validated.errors); setMessage("Review the highlighted fields."); return; }
    setSubmitting(true); setErrors({}); setMessage(null);
    try { const created = await createJudgeConfiguration(validated.payload); onCreated(created); setDraft(initialDraft); setMessage(`Created judge configuration “${created.name}”.`); }
    catch (reason) { setMessage(reason instanceof CalibrationApiError || reason instanceof Error ? reason.message : "Unable to create the judge configuration."); }
    finally { setSubmitting(false); }
  }
  return (
    <section id="judge-configurations" aria-labelledby="configurations-title" className="scroll-mt-6 border-t pt-7">
      <div className="flex items-end justify-between gap-3"><div><p className="font-mono text-xs font-semibold text-accent">STEP 03</p><h2 id="configurations-title" className="mt-2 text-2xl font-semibold">Judge configurations</h2><p className="mt-1 max-w-3xl text-sm leading-6 text-muted">A configuration describes how the LLM judge evaluates one candidate. API keys remain server-side and are never stored here.</p></div><span className="font-mono text-xs text-muted">{configurations.length} LOADED</span></div>
      <div className="mt-5 grid gap-6 xl:grid-cols-[minmax(21rem,0.8fr)_minmax(32rem,1.2fr)]">
        <form onSubmit={submit} noValidate className="border bg-surface p-4"><h3 className="font-semibold">Create judge configuration</h3>{message && <p role={Object.values(errors).some(Boolean) ? "alert" : "status"} className="mt-3 border bg-canvas px-3 py-2 text-sm">{message}</p>}<div className="mt-4 grid gap-3"><Field label="Name" error={errors.name} required><input value={draft.name} onChange={(event) => update("name", event.target.value)} maxLength={200} className={inputClass} /></Field><Field label="Description" error={errors.description}><textarea value={draft.description} onChange={(event) => update("description", event.target.value)} maxLength={2_000} rows={2} className={`${inputClass} py-2`} /></Field><Field label="Provider"><input value="OpenAI" disabled className={`${inputClass} bg-canvas text-muted`} /></Field><Field label="Model" error={errors.model} required><input value={draft.model} onChange={(event) => update("model", event.target.value)} maxLength={200} placeholder="gpt-5-mini" className={inputClass} /></Field><Field label="Rubric / instructions" error={errors.rubric} required hint="Explain exactly when the candidate should pass or fail."><textarea value={draft.rubric} onChange={(event) => update("rubric", event.target.value)} maxLength={8_000} rows={6} className={`${inputClass} py-2`} /></Field><div className="grid gap-3 sm:grid-cols-2"><Field label="Timeout (seconds)" error={errors.timeoutSeconds} required><input type="number" min={5} max={300} value={draft.timeoutSeconds} onChange={(event) => update("timeoutSeconds", event.target.value)} className={inputClass} /></Field><Field label="Output-token limit" error={errors.maxOutputTokens} required><input type="number" min={32} max={1_000} value={draft.maxOutputTokens} onChange={(event) => update("maxOutputTokens", event.target.value)} className={inputClass} /></Field></div></div><p className="mt-3 text-xs text-muted">Structured output schema 1 · Configuration schema 1</p><button type="submit" disabled={submitting} className={`mt-4 ${primaryButtonClass}`}>{submitting ? "Creating…" : "Create configuration"}</button></form>
        {configurations.length === 0 ? <ViewState title="No judge configurations" detail="Create the first bounded OpenAI judge specification." /> : <ol className="divide-y border bg-surface" aria-label="Judge configurations">{configurations.map((item) => <li key={item.id} className="p-4"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">{item.name}</h3><span className="border bg-canvas px-2 py-1 text-xs font-semibold">{item.provider} · {item.model}</span></div>{item.description && <p className="mt-2 text-sm leading-6 text-muted">{item.description}</p>}<p className="mt-3 whitespace-pre-wrap text-sm leading-6">{item.rubric}</p><dl className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4"><div><dt className="text-muted">Timeout</dt><dd className="mt-1 font-mono">{item.timeout_seconds}s</dd></div><div><dt className="text-muted">Output limit</dt><dd className="mt-1 font-mono">{item.max_output_tokens}</dd></div><div><dt className="text-muted">Schemas</dt><dd className="mt-1 font-mono">{item.configuration_version}/{item.output_schema_version}</dd></div><div><dt className="text-muted">Created</dt><dd className="mt-1">{formatTimestamp(item.created_at)}</dd></div></dl></li>)}</ol>}
      </div>
    </section>
  );
}
