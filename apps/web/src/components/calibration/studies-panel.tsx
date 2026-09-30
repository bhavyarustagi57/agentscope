"use client";

import { useState } from "react";

import { CalibrationApiError, createStudy, type CalibrationStudy, type ReferenceSet } from "@/lib/calibration-api";
import { buildStudyPayload } from "@/lib/calibration-ui";
import { formatTimestamp } from "@/lib/trace-explorer";

import { Field, ViewState, inputClass, primaryButtonClass } from "./shared";

export function StudiesPanel({ referenceSets, studies, onCreated }: {
  referenceSets: ReferenceSet[]; studies: CalibrationStudy[]; onCreated: (item: CalibrationStudy) => void;
}) {
  const frozenSets = referenceSets.filter((item) => item.status === "frozen");
  const [name, setName] = useState(""); const [description, setDescription] = useState("");
  const [referenceSetId, setReferenceSetId] = useState(""); const [errors, setErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null); const [submitting, setSubmitting] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const validated = buildStudyPayload({ name, description, referenceSetId });
    if (!validated.payload) { setErrors(validated.errors); setMessage("Review the highlighted fields."); return; }
    setSubmitting(true); setErrors({}); setMessage(null);
    try { const created = await createStudy(validated.payload); onCreated(created); setName(""); setDescription(""); setReferenceSetId(""); setMessage(`Created immutable study “${created.name}”.`); }
    catch (reason) { setMessage(reason instanceof CalibrationApiError || reason instanceof Error ? reason.message : "Unable to create the study."); }
    finally { setSubmitting(false); }
  }

  return (
    <section id="studies" aria-labelledby="studies-title" className="scroll-mt-6 border-t pt-7">
      <div className="flex items-end justify-between gap-3"><div><p className="font-mono text-xs font-semibold text-accent">STEP 02</p><h2 id="studies-title" className="mt-2 text-2xl font-semibold">Calibration studies</h2><p className="mt-1 max-w-3xl text-sm leading-6 text-muted">A study copies the ordered trace IDs and authoritative labels from one complete frozen set. Later activity elsewhere cannot change this snapshot.</p></div><span className="font-mono text-xs text-muted">{studies.length} LOADED</span></div>
      <div className="mt-5 grid gap-6 xl:grid-cols-[minmax(19rem,0.7fr)_minmax(34rem,1.3fr)]">
        <form onSubmit={submit} noValidate className="border bg-surface p-4">
          <h3 className="font-semibold">Create study</h3>
          {message && <p role={Object.values(errors).some(Boolean) ? "alert" : "status"} className="mt-3 border bg-canvas px-3 py-2 text-sm">{message}</p>}
          <div className="mt-4 grid gap-3"><Field label="Name" error={errors.name} required><input value={name} onChange={(event) => setName(event.target.value)} maxLength={200} className={inputClass} /></Field><Field label="Description" error={errors.description}><textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={2_000} rows={3} className={`${inputClass} py-2`} /></Field><Field label="Frozen reference set" error={errors.referenceSetId} required hint="Only complete, frozen sets are eligible."><select value={referenceSetId} onChange={(event) => setReferenceSetId(event.target.value)} className={inputClass}><option value="">Choose a reference set</option>{frozenSets.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.subject_count} subjects</option>)}</select></Field></div>
          <button type="submit" disabled={submitting || frozenSets.length === 0} className={`mt-4 ${primaryButtonClass}`}>{submitting ? "Creating…" : "Create immutable study"}</button>
          {frozenSets.length === 0 && <p className="mt-3 text-sm text-muted">Freeze a complete reference set to unlock study creation.</p>}
        </form>
        {studies.length === 0 ? <ViewState title="No calibration studies" detail="Create one from a complete frozen reference set." /> : (
          <ol className="divide-y border bg-surface" aria-label="Calibration studies">
            {studies.map((study) => { const source = referenceSets.find((item) => item.id === study.reference_set_id); return <li key={study.id} className="p-4"><div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">{study.name}</h3><span className="border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-semibold text-emerald-900">Immutable snapshot</span></div><p className="mt-2 text-sm leading-6 text-muted">{study.description ?? "No description provided."}</p><dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2"><div><dt className="text-xs font-semibold text-muted">Source reference set</dt><dd className="mt-1">{source?.name ?? study.reference_set_id}</dd></div><div><dt className="text-xs font-semibold text-muted">Created</dt><dd className="mt-1">{formatTimestamp(study.created_at)}</dd></div></dl></div><span className="shrink-0 font-mono text-sm font-semibold">{study.subject_count} subjects</span></div></li>; })}
          </ol>
        )}
      </div>
    </section>
  );
}
