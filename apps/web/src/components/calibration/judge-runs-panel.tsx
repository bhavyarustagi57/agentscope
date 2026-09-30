"use client";

import Link from "next/link";
import { useState } from "react";

import { CalibrationApiError, createJudgeRun, type CalibrationStudy, type JudgeConfiguration, type JudgeRun } from "@/lib/calibration-api";
import { formatTimestamp } from "@/lib/trace-explorer";

import { Field, JudgeRunStatusBadge, ViewState, inputClass, primaryButtonClass } from "./shared";

export function JudgeRunsPanel({ studies, configurations, runs, onCreated }: {
  studies: CalibrationStudy[]; configurations: JudgeConfiguration[]; runs: JudgeRun[]; onCreated: (item: JudgeRun) => void;
}) {
  const [studyId, setStudyId] = useState(""); const [configurationId, setConfigurationId] = useState("");
  const [message, setMessage] = useState<string | null>(null); const [submitting, setSubmitting] = useState(false);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!studyId || !configurationId) { setMessage("Choose both a calibration study and judge configuration."); return; }
    setSubmitting(true); setMessage(null);
    try { const created = await createJudgeRun(studyId, configurationId); onCreated(created); setMessage("Created an immutable pending judge run. Open it to submit execution."); }
    catch (reason) { setMessage(reason instanceof CalibrationApiError || reason instanceof Error ? reason.message : "Unable to create the judge run."); }
    finally { setSubmitting(false); }
  }
  return (
    <section id="judge-runs" aria-labelledby="runs-title" className="scroll-mt-6 border-t pt-7">
      <div className="flex items-end justify-between gap-3"><div><p className="font-mono text-xs font-semibold text-accent">STEP 04</p><h2 id="runs-title" className="mt-2 text-2xl font-semibold">Judge runs</h2><p className="mt-1 max-w-3xl text-sm leading-6 text-muted">Each run snapshots one study population and one judge configuration. Open a run for execution, progress, calibration evidence, disagreements, and comparison.</p></div><span className="font-mono text-xs text-muted">{runs.length} LOADED</span></div>
      <div className="mt-5 grid gap-6 xl:grid-cols-[minmax(19rem,0.7fr)_minmax(34rem,1.3fr)]">
        <form onSubmit={submit} className="border bg-surface p-4"><h3 className="font-semibold">Create judge run</h3>{message && <p role={message.startsWith("Created") ? "status" : "alert"} className="mt-3 border bg-canvas px-3 py-2 text-sm">{message}</p>}<div className="mt-4 grid gap-3"><Field label="Calibration study" required><select value={studyId} onChange={(event) => setStudyId(event.target.value)} className={inputClass}><option value="">Choose a study</option>{studies.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.subject_count} subjects</option>)}</select></Field><Field label="Judge configuration" required><select value={configurationId} onChange={(event) => setConfigurationId(event.target.value)} className={inputClass}><option value="">Choose a configuration</option>{configurations.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.model}</option>)}</select></Field></div><button type="submit" disabled={submitting || !studies.length || !configurations.length} className={`mt-4 ${primaryButtonClass}`}>{submitting ? "Creating…" : "Create judge run"}</button>{(!studies.length || !configurations.length) && <p className="mt-3 text-sm text-muted">Create at least one study and judge configuration first.</p>}</form>
        {runs.length === 0 ? <ViewState title="No judge runs" detail="Create a run after the study and judge configuration are ready." /> : <ol className="divide-y border bg-surface" aria-label="Judge runs">{runs.map((run) => { const study = studies.find((item) => item.id === run.study_id); const configuration = configurations.find((item) => item.id === run.configuration_id); return <li key={run.id}><Link href={`/calibration/runs/${encodeURIComponent(run.id)}`} className="grid gap-3 p-4 outline-none hover:bg-canvas/70 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent sm:grid-cols-[minmax(13rem,1fr)_minmax(12rem,1fr)_auto] sm:items-center"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">{configuration?.name ?? run.model}</h3><JudgeRunStatusBadge status={run.status} /></div><p className="mt-2 text-sm text-muted">{study?.name ?? run.study_id}</p></div><div className="text-sm"><p>{run.provider} · {run.model}</p><p className="mt-1 text-xs text-muted">Created {formatTimestamp(run.created_at)}</p></div><div className="text-right"><p className="font-mono text-sm font-semibold">{run.result_count}/{run.subject_count}</p><p className="mt-1 text-xs text-muted">results</p></div></Link></li>; })}</ol>}
      </div>
    </section>
  );
}
