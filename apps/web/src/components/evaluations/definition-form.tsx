"use client";

import { cloneElement, isValidElement, useId, useState, type ReactElement, type ReactNode } from "react";

import {
  createEvaluationDefinition,
  EvaluationApiError,
  type EvaluationDefinition,
  type EvaluatorKind,
} from "@/lib/evaluation-api";
import { buildDefinitionPayload, type DefinitionDraft } from "@/lib/evaluation-ui";

const initialDraft: DefinitionDraft = {
  name: "",
  description: "",
  kind: "exact_match",
  expected: "",
  substring: "",
  caseSensitive: true,
  minimum: "",
  maximum: "",
};

const inputClass = "min-h-11 border bg-white px-3 font-normal outline-none focus-visible:ring-2 focus-visible:ring-accent";

export function DefinitionForm({ onCreated }: { onCreated: (definition: EvaluationDefinition) => void }) {
  const [draft, setDraft] = useState(initialDraft);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  function update<K extends keyof DefinitionDraft>(key: K, value: DefinitionDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: "", threshold: key === "minimum" || key === "maximum" ? "" : current.threshold }));
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const validated = buildDefinitionPayload(draft);
    if (!validated.payload) {
      setErrors(validated.errors);
      setMessage("Review the highlighted definition fields.");
      return;
    }
    setSubmitting(true);
    setErrors({});
    setMessage(null);
    try {
      const definition = await createEvaluationDefinition(validated.payload);
      onCreated(definition);
      setDraft(initialDraft);
      setMessage(`Created definition “${definition.name}”.`);
    } catch (reason) {
      setMessage(reason instanceof EvaluationApiError ? reason.message : "Unable to create the definition.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="border bg-surface p-5" noValidate>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">Create a definition</h2>
          <p className="mt-1 text-sm leading-6 text-muted">A reusable deterministic specification. Each run stores its own snapshot.</p>
        </div>
        <span className="font-mono text-xs text-muted">SPEC</span>
      </div>

      {message && <p role={Object.values(errors).some(Boolean) ? "alert" : "status"} className="mt-4 border bg-canvas px-3 py-2 text-sm">{message}</p>}

      <div className="mt-5 grid gap-4">
        <Field label="Name" error={errors.name} required>
          <input value={draft.name} onChange={(event) => update("name", event.target.value)} maxLength={200} className={inputClass} aria-invalid={Boolean(errors.name)} />
        </Field>
        <Field label="Description" error={errors.description}>
          <textarea value={draft.description} onChange={(event) => update("description", event.target.value)} maxLength={2_000} rows={3} className={`${inputClass} py-2`} />
        </Field>
        <Field label="Evaluator" required>
          <select value={draft.kind} onChange={(event) => update("kind", event.target.value as EvaluatorKind)} className={inputClass}>
            <option value="exact_match">Exact match</option>
            <option value="contains">Contains</option>
            <option value="numeric_threshold">Numeric threshold</option>
          </select>
        </Field>

        {draft.kind === "exact_match" && (
          <Field label="Expected text" error={errors.expected} required hint="Compared with the trace’s top-level output.">
            <input value={draft.expected} onChange={(event) => update("expected", event.target.value)} className={inputClass} aria-invalid={Boolean(errors.expected)} />
          </Field>
        )}
        {draft.kind === "contains" && (
          <Field label="Required substring" error={errors.substring} required hint="Literal substring; regular expressions are not used.">
            <input value={draft.substring} onChange={(event) => update("substring", event.target.value)} maxLength={4_000} className={inputClass} aria-invalid={Boolean(errors.substring)} />
          </Field>
        )}
        {draft.kind === "numeric_threshold" && (
          <fieldset className="border p-4">
            <legend className="px-1 text-sm font-semibold">Inclusive numeric bounds</legend>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Minimum">
                <input type="number" step="any" value={draft.minimum} onChange={(event) => update("minimum", event.target.value)} className={inputClass} />
              </Field>
              <Field label="Maximum">
                <input type="number" step="any" value={draft.maximum} onChange={(event) => update("maximum", event.target.value)} className={inputClass} />
              </Field>
            </div>
            {errors.threshold && <p role="alert" className="mt-2 text-sm text-red-800">{errors.threshold}</p>}
          </fieldset>
        )}

        {draft.kind !== "numeric_threshold" && (
          <label className="flex min-h-11 items-center gap-3 text-sm font-semibold">
            <input type="checkbox" checked={draft.caseSensitive} onChange={(event) => update("caseSensitive", event.target.checked)} className="h-5 w-5 accent-accent" />
            Case-sensitive comparison
          </label>
        )}
      </div>

      <button type="submit" disabled={submitting} className="mt-5 min-h-11 bg-panel px-5 text-sm font-semibold text-white outline-none hover:bg-accent disabled:cursor-wait disabled:opacity-60 focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2">
        {submitting ? "Creating…" : "Create definition"}
      </button>
    </form>
  );
}

function Field({ label, error, hint, required = false, children }: { label: string; error?: string; hint?: string; required?: boolean; children: ReactNode }) {
  const id = useId();
  const describedBy = [hint ? `${id}-hint` : "", error ? `${id}-error` : ""].filter(Boolean).join(" ") || undefined;
  const control = isValidElement(children) ? cloneElement(children as ReactElement<{ "aria-describedby"?: string; "aria-invalid"?: boolean }>, { "aria-describedby": describedBy, "aria-invalid": Boolean(error) }) : children;
  return (
    <label className="grid gap-1.5 text-sm font-semibold">
      <span>{label}{required && <span className="text-red-800"> (required)</span>}</span>
      {control}
      {hint && <span id={`${id}-hint`} className="text-xs font-normal text-muted">{hint}</span>}
      {error && <span id={`${id}-error`} role="alert" className="text-sm font-normal text-red-800">{error}</span>}
    </label>
  );
}
