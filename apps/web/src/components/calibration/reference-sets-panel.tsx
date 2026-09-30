"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  beginReferenceLabeling,
  CalibrationApiError,
  createReferenceSet,
  defineReferenceSubjects,
  freezeReferenceSet,
  getReferenceSet,
  listReferenceSubjects,
  setReferenceLabel,
  writeHumanAnnotation,
  type HumanLabel,
  type ReferenceSet,
  type ReferenceSubject,
} from "@/lib/calibration-api";
import { buildReferenceSetPayload, buildTraceHref, parseTraceIds, referenceSetActions } from "@/lib/calibration-ui";
import { formatTimestamp } from "@/lib/trace-explorer";
import { ConfirmAction } from "@/components/workflow-controls";

import { Field, Metric, ReferenceStatusBadge, ViewState, buttonClass, inputClass, primaryButtonClass } from "./shared";

export function ReferenceSetsPanel({ referenceSets, onCreated, onUpdated }: {
  referenceSets: ReferenceSet[];
  onCreated: (item: ReferenceSet) => void;
  onUpdated: (item: ReferenceSet) => void;
}) {
  const [selectedId, setSelectedId] = useState("");
  const effectiveSelectedId = selectedId || referenceSets[0]?.id || "";
  const selected = useMemo(() => referenceSets.find((item) => item.id === effectiveSelectedId) ?? null, [referenceSets, effectiveSelectedId]);
  const [subjects, setSubjects] = useState<ReferenceSubject[]>([]);
  const [subjectsKey, setSubjectsKey] = useState<string | null>(null);
  const [subjectsError, setSubjectsError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [traceIds, setTraceIds] = useState("");
  const [traceError, setTraceError] = useState<string | null>(null);

  async function loadSubjects(id: string, signal?: AbortSignal) {
    const all: ReferenceSubject[] = [];
    for (let offset = 0; offset <= 400; offset += 100) {
      const page = await listReferenceSubjects(id, offset, signal);
      all.push(...page.items);
      if (!page.has_more) return all;
    }
    return all;
  }

  useEffect(() => {
    if (!effectiveSelectedId) return;
    const controller = new AbortController();
    loadSubjects(effectiveSelectedId, controller.signal)
      .then((items) => { setSubjects(items); setSubjectsError(null); setSubjectsKey(effectiveSelectedId); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) { setSubjectsError(reason instanceof Error ? reason.message : "Unable to load reference subjects."); setSubjectsKey(effectiveSelectedId); } });
    return () => controller.abort();
  }, [effectiveSelectedId]);

  async function refreshSelected() {
    if (!effectiveSelectedId) return;
    const [nextSet, nextSubjects] = await Promise.all([getReferenceSet(effectiveSelectedId), loadSubjects(effectiveSelectedId)]);
    onUpdated(nextSet);
    setSubjects(nextSubjects);
    setSubjectsKey(effectiveSelectedId);
  }

  async function lifecycleAction(name: string, operation: () => Promise<ReferenceSet>) {
    setBusy(name); setActionError(null);
    try { onUpdated(await operation()); await refreshSelected(); }
    catch (reason) { setActionError(apiMessage(reason, "Unable to update the reference set.")); }
    finally { setBusy(null); }
  }

  async function defineSubjects() {
    if (!selected) return;
    const parsed = parseTraceIds(traceIds);
    if (parsed.error) { setTraceError(parsed.error); return; }
    setBusy("subjects"); setTraceError(null); setActionError(null);
    try {
      onUpdated(await defineReferenceSubjects(selected.id, parsed.traceIds));
      setTraceIds("");
      await refreshSelected();
    } catch (reason) { setActionError(apiMessage(reason, "Unable to define reference subjects.")); }
    finally { setBusy(null); }
  }

  const actions = selected ? referenceSetActions(selected.status, selected.subject_count, selected.reference_count) : null;

  return (
    <section id="reference-sets" aria-labelledby="reference-sets-title" className="scroll-mt-6 border-t pt-7">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div><p className="font-mono text-xs font-semibold text-accent">STEP 01</p><h2 id="reference-sets-title" className="mt-2 text-2xl font-semibold">Human reference sets</h2><p className="mt-1 max-w-3xl text-sm leading-6 text-muted">Raw annotations record individual human judgments. Authoritative labels are explicit adjudications used as calibration ground truth—never a majority vote.</p></div>
        <span className="font-mono text-xs text-muted">{referenceSets.length} LOADED</span>
      </div>

      <div className="mt-5 grid gap-6 xl:grid-cols-[minmax(19rem,0.7fr)_minmax(34rem,1.3fr)]">
        <div className="space-y-5">
          <CreateReferenceSetForm onCreated={(item) => { onCreated(item); setSelectedId(item.id); }} />
          {referenceSets.length === 0 ? <ViewState title="No reference sets" detail="Create the first set, then define its trace population." /> : (
            <div className="border bg-surface p-4">
              <label className="grid gap-2 text-sm font-semibold">Inspect reference set
                <select value={effectiveSelectedId} onChange={(event) => setSelectedId(event.target.value)} className={inputClass}>
                  {referenceSets.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.status}</option>)}
                </select>
              </label>
            </div>
          )}
        </div>

        {selected && actions && (
          <div className="min-w-0 border bg-surface p-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div><div className="flex flex-wrap items-center gap-2"><h3 className="text-lg font-semibold">{selected.name}</h3><ReferenceStatusBadge status={selected.status} /></div><p className="mt-2 text-sm leading-6 text-muted">{selected.description ?? "No description provided."}</p><p className="mt-2 text-xs text-muted">Created {formatTimestamp(selected.created_at)}</p></div>
              {selected.status === "frozen" && <span className="border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-900">Immutable since {formatTimestamp(selected.frozen_at)}</span>}
            </div>
            <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
              <Metric label="Subjects" value={selected.subject_count} /><Metric label="Annotations" value={selected.annotation_count} /><Metric label="Authoritative" value={selected.reference_count} /><Metric label="Unlabeled" value={selected.unlabeled_count} /><Metric label="Passed" value={selected.passed_count} /><Metric label="Failed" value={selected.failed_count} />
            </div>
            {actionError && <p role="alert" className="mt-4 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900">{actionError}</p>}

            {actions.canDefineSubjects && (
              <div className="mt-6 border-t pt-5">
                <Field label="Trace IDs" error={traceError ?? undefined} required hint="Enter one or more existing trace IDs separated by commas, spaces, or new lines. Membership is immutable after labeling starts.">
                  <textarea value={traceIds} onChange={(event) => setTraceIds(event.target.value)} rows={4} className={`${inputClass} py-2 font-mono text-sm`} aria-invalid={Boolean(traceError)} />
                </Field>
                <button type="button" onClick={defineSubjects} disabled={busy !== null} className={`mt-3 ${buttonClass}`}>{busy === "subjects" ? "Saving…" : selected.subject_count ? "Confirm subjects" : "Define subjects"}</button>
              </div>
            )}
            {actions.canBeginLabeling && <button type="button" onClick={() => lifecycleAction("labeling", () => beginReferenceLabeling(selected.id))} disabled={busy !== null} className={`mt-5 ${primaryButtonClass}`}>{busy === "labeling" ? "Starting…" : "Begin labeling"}</button>}
            {selected.status === "draft" && selected.subject_count === 0 && <p className="mt-4 text-sm text-muted">Define at least one subject before labeling can begin.</p>}
            {actions.canFreeze && <div className="mt-5"><ConfirmAction triggerLabel="Freeze complete reference set" confirmLabel="Confirm reference freeze" description="This permanently freezes membership, annotations, and authoritative labels. Create a new reference set if those inputs must change." busy={busy !== null} onConfirm={() => lifecycleAction("freeze", () => freezeReferenceSet(selected.id))} /></div>}
            {selected.status === "labeling" && !actions.canFreeze && <p className="mt-4 border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950">{selected.unlabeled_count} authoritative label{selected.unlabeled_count === 1 ? "" : "s"} remain before freezing.</p>}

            <div className="mt-7 border-t pt-5">
              <h4 className="font-semibold">Subjects</h4>
              {subjectsKey !== effectiveSelectedId ? <div aria-busy="true" aria-label="Loading reference subjects" className="mt-3 h-32 animate-pulse border bg-canvas" /> : subjectsError ? <p role="alert" className="mt-3 text-sm text-red-800">{subjectsError}</p> : subjects.length === 0 ? <p className="mt-3 text-sm text-muted">No subjects defined yet.</p> : (
                <ol className="mt-3 divide-y border" aria-label="Reference subjects">
                  {subjects.map((subject) => <li key={subject.trace_id} className="grid gap-3 p-3 sm:grid-cols-[minmax(12rem,1fr)_auto_auto] sm:items-center"><div className="min-w-0"><Link href={buildTraceHref(subject.trace_id)} className="font-semibold text-accent underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-accent">{subject.trace_name}</Link><p className="mt-1 truncate font-mono text-xs text-muted" title={subject.trace_id}>{subject.trace_id}</p></div><div className="text-sm"><p><span className="text-muted">Raw annotations:</span> <strong>{subject.annotation_count}</strong></p><p className="mt-1"><span className="text-muted">Authoritative:</span> <strong className="capitalize">{subject.reference_label ?? "Unlabeled"}</strong></p></div><span className="font-mono text-xs text-muted">#{subject.position + 1}</span></li>)}
                </ol>
              )}
            </div>

            {actions.canLabel && subjects.length > 0 && <SubjectLabelEditor referenceSetId={selected.id} subjects={subjects} onSaved={refreshSelected} />}
            {selected.status === "frozen" && <p className="mt-5 border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm text-emerald-950">This set is frozen. Membership, raw annotations, and authoritative labels are immutable; create a study to use this exact human reference population.</p>}
          </div>
        )}
      </div>
    </section>
  );
}

function CreateReferenceSetForm({ onCreated }: { onCreated: (item: ReferenceSet) => void }) {
  const [name, setName] = useState(""); const [description, setDescription] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({}); const [message, setMessage] = useState<string | null>(null); const [submitting, setSubmitting] = useState(false);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); const validated = buildReferenceSetPayload({ name, description });
    if (!validated.payload) { setErrors(validated.errors); setMessage("Review the highlighted fields."); return; }
    setSubmitting(true); setErrors({}); setMessage(null);
    try { const created = await createReferenceSet(validated.payload); onCreated(created); setName(""); setDescription(""); setMessage(`Created “${created.name}”.`); }
    catch (reason) { setMessage(apiMessage(reason, "Unable to create the reference set.")); }
    finally { setSubmitting(false); }
  }
  return <form onSubmit={submit} noValidate className="border bg-surface p-4"><h3 className="font-semibold">Create reference set</h3><p className="mt-1 text-sm leading-6 text-muted">A bounded human-labelled population.</p>{message && <p role={Object.values(errors).some(Boolean) ? "alert" : "status"} className="mt-3 border bg-canvas px-3 py-2 text-sm">{message}</p>}<div className="mt-4 grid gap-3"><Field label="Name" error={errors.name} required><input value={name} onChange={(event) => setName(event.target.value)} maxLength={200} className={inputClass} /></Field><Field label="Description" error={errors.description}><textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={2_000} rows={3} className={`${inputClass} py-2`} /></Field></div><button type="submit" disabled={submitting} className={`mt-4 ${primaryButtonClass}`}>{submitting ? "Creating…" : "Create reference set"}</button></form>;
}

function SubjectLabelEditor({ referenceSetId, subjects, onSaved }: { referenceSetId: string; subjects: ReferenceSubject[]; onSaved: () => Promise<void> }) {
  const [traceId, setTraceId] = useState(subjects[0]?.trace_id ?? ""); const [annotator, setAnnotator] = useState(""); const [label, setLabel] = useState<HumanLabel | "">(""); const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState<"annotation" | "reference" | null>(null); const [message, setMessage] = useState<string | null>(null);
  const validAnnotator = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(annotator);
  async function save(kind: "annotation" | "reference") {
    if (!validAnnotator) { setMessage("Annotator/adjudicator ID must use letters, numbers, dot, underscore, or hyphen."); return; }
    if (!label) { setMessage("Choose an explicit passed or failed label."); return; }
    setBusy(kind); setMessage(null);
    try {
      const payload = { label, annotator_id: annotator, rationale: rationale.trim() || null };
      if (kind === "annotation") await writeHumanAnnotation(referenceSetId, { trace_id: traceId, ...payload, source: "manual" });
      else await setReferenceLabel(referenceSetId, traceId, payload);
      await onSaved(); setMessage(kind === "annotation" ? "Saved the raw human annotation." : "Set the authoritative reference label.");
    } catch (reason) { setMessage(apiMessage(reason, "Unable to save the label.")); }
    finally { setBusy(null); }
  }
  return <div className="mt-6 border-t pt-5"><h4 className="font-semibold">Label a subject</h4><p className="mt-1 text-sm leading-6 text-muted"><strong>Raw annotation</strong> records one person’s judgment. <strong>Authoritative label</strong> is the explicit adjudicated answer used by calibration. Saving one never infers the other.</p>{message && <p role="status" aria-live="polite" className="mt-3 border bg-canvas px-3 py-2 text-sm">{message}</p>}<div className="mt-4 grid gap-3 sm:grid-cols-2"><Field label="Subject" required><select value={traceId} onChange={(event) => setTraceId(event.target.value)} className={inputClass}>{subjects.map((item) => <option key={item.trace_id} value={item.trace_id}>{item.trace_name} · {item.reference_label ?? "unlabeled"}</option>)}</select></Field><Field label="Annotator / adjudicator ID" required><input value={annotator} onChange={(event) => setAnnotator(event.target.value)} maxLength={100} className={inputClass} aria-invalid={Boolean(annotator) && !validAnnotator} /></Field><Field label="Label" required><select value={label} onChange={(event) => setLabel(event.target.value as HumanLabel | "")} className={inputClass}><option value="">Choose a label</option><option value="passed">Passed</option><option value="failed">Failed</option></select></Field><Field label="Rationale" hint="Plain text, maximum 4,000 characters."><textarea value={rationale} onChange={(event) => setRationale(event.target.value)} maxLength={4_000} rows={3} className={`${inputClass} py-2`} /></Field></div><div className="mt-4 flex flex-wrap gap-3"><button type="button" onClick={() => save("annotation")} disabled={busy !== null} className={buttonClass}>{busy === "annotation" ? "Saving…" : "Save raw annotation"}</button><button type="button" onClick={() => save("reference")} disabled={busy !== null} className={primaryButtonClass}>{busy === "reference" ? "Saving…" : "Set authoritative label"}</button></div></div>;
}

function apiMessage(reason: unknown, fallback: string): string {
  return reason instanceof CalibrationApiError || reason instanceof Error ? reason.message : fallback;
}
