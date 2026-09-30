"use client";

import { useEffect, useState } from "react";

import { inputClass, Metric, primaryButtonClass, ViewState } from "@/components/experiments/shared";
import { Breadcrumbs } from "@/components/product-context";
import {
  createBisectionAnalysis,
  getBisectionAnalysis,
  getBisectionSession,
  listBisectionAnalyses,
  listExecutionRuns,
  startBisectionAnalysis,
  type BisectionAnalysis,
  type BisectionSession,
  type ExecutionRun,
} from "@/lib/regression-api";
import {
  buildProbeConfiguration,
  evidenceSourceLabel,
  executionOutcomeLabel,
  shortSha,
  shouldPollBisectionAnalysis,
  sortAnalysisSteps,
  terminalReasonLabel,
} from "@/lib/regression-ui";

import { EvidenceBadge, Field, FullSha } from "./shared";

const initialProbe = { executable: "python", argumentsText: "", workingDirectory: ".", timeoutSeconds: "30", maxStdoutBytes: "65536", maxStderrBytes: "65536" };

export function BisectionSessionDetail({ sessionId }: { sessionId: string }) {
  const [session, setSession] = useState<BisectionSession | null>(null);
  const [analyses, setAnalyses] = useState<BisectionAnalysis[]>([]);
  const [runs, setRuns] = useState<ExecutionRun[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [probe, setProbe] = useState(initialProbe);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([getBisectionSession(sessionId, controller.signal), listBisectionAnalyses(sessionId, controller.signal), listExecutionRuns(sessionId, controller.signal)])
      .then(([sessionValue, analysisPage, runPage]) => {
        setSession(sessionValue); setAnalyses(analysisPage.items); setRuns(runPage.items);
        setSelectedId((value) => value || analysisPage.items[0]?.id || ""); setError(null);
      })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load bisection evidence."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [retry, sessionId]);

  const selected = analyses.find((analysis) => analysis.id === selectedId) ?? null;
  useEffect(() => {
    if (!selected || !shouldPollBisectionAnalysis(selected.status)) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined; let stopped = false;
    const poll = async () => {
      try {
        const [analysis, runPage] = await Promise.all([getBisectionAnalysis(selected.id, controller.signal), listExecutionRuns(sessionId, controller.signal)]);
        setAnalyses((items) => items.map((item) => item.id === analysis.id ? analysis : item)); setRuns(runPage.items); setError(null);
        if (!shouldPollBisectionAnalysis(analysis.status)) return;
      } catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to refresh analysis progress."); }
      if (!stopped) timer = setTimeout(poll, 5_000);
    };
    timer = setTimeout(poll, 5_000);
    return () => { stopped = true; controller.abort(); if (timer) clearTimeout(timer); };
  }, [selected, sessionId]);

  async function runAnalysis(event: React.FormEvent) {
    event.preventDefault();
    const built = buildProbeConfiguration(probe); setFieldErrors(built.errors);
    if (Object.keys(built.errors).length) return;
    setBusy(true); setError(null);
    try {
      const created = await createBisectionAnalysis(sessionId, built.configuration);
      setAnalyses((items) => [created, ...items]); setSelectedId(created.id);
      await startBisectionAnalysis(created.id);
      const queued = await getBisectionAnalysis(created.id);
      setAnalyses((items) => items.map((item) => item.id === queued.id ? queued : item));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to start bisection analysis."); }
    finally { setBusy(false); }
  }

  if (loading) return <div aria-busy="true" aria-label="Loading bisection session" className="h-96 animate-pulse border bg-surface" />;
  if (error && !session) return <ViewState title="Couldn’t load bisection session" detail={error} role="alert" headingLevel="h1"><button type="button" onClick={() => { setLoading(true); setRetry((value) => value + 1); }} className={`mt-4 ${primaryButtonClass}`}>Retry</button></ViewState>;
  if (!session) return <ViewState title="Bisection session unavailable" detail="The server returned no session evidence." headingLevel="h1" />;

  return <div className="mx-auto max-w-[90rem]">
    <Breadcrumbs items={[{ label: "Regressions", href: "/regressions" }, { label: "Regression check", href: `/regressions/${encodeURIComponent(session.regression_check_id)}` }, { label: "Bisection session" }]} />
    <header className="border-b pb-6"><p className="font-mono text-xs font-semibold tracking-[0.16em] text-accent">REGRESSIONS / BISECTION SESSION</p><h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">Git bisection analysis</h1><p className="mt-2 max-w-4xl text-sm leading-6 text-muted">Execute an argv-based probe in isolated worktrees and follow the server’s persisted decision evidence. Attribution identifies an observed boundary; it does not by itself prove causal root cause.</p></header>
    {error && <p role="alert" className="mt-5 break-words border border-red-300 bg-red-50 p-4 text-sm text-red-900">{error}</p>}
    <section aria-labelledby="repository-title" className="mt-7 border bg-surface p-5"><h2 id="repository-title" className="text-xl font-semibold">Frozen repository plan</h2><p className="mt-1 text-sm text-muted">The server verified a local repository, then froze its fingerprint and commit ancestry. The local filesystem path is intentionally not displayed.</p><dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><div><dt className="text-xs font-semibold text-muted">Repository fingerprint</dt><dd className="mt-1"><FullSha value={session.repository_fingerprint} /></dd></div><div><dt className="text-xs font-semibold text-muted">Planning HEAD</dt><dd className="mt-1"><FullSha value={session.repository_head_sha} /></dd></div><div><dt className="text-xs font-semibold text-muted">Frozen ancestry</dt><dd className="mt-1 font-mono text-sm">{session.commit_count} commits</dd></div><div><dt className="text-xs font-semibold text-muted">Baseline commit</dt><dd className="mt-1"><FullSha value={session.baseline_commit_sha} /></dd></div><div><dt className="text-xs font-semibold text-muted">Candidate commit</dt><dd className="mt-1"><FullSha value={session.candidate_commit_sha} /></dd></div></dl><details className="mt-5 border bg-white p-4"><summary className="cursor-pointer font-semibold">Ordered frozen commit range ({session.commits.length})</summary><ol className="mt-4 space-y-3">{session.commits.map((commit) => <li key={commit.commit_sha} className="grid gap-1 border-t pt-3 sm:grid-cols-[4rem_minmax(0,1fr)]"><span className="font-mono text-xs text-muted">#{commit.position}</span><div className="min-w-0"><FullSha value={commit.commit_sha} /><p className="mt-1 break-words text-sm">{commit.subject}</p><time className="mt-1 block text-xs text-muted" dateTime={commit.committed_at}>{commit.committed_at}</time></div></li>)}</ol></details></section>
    <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(20rem,0.75fr)_minmax(0,1.25fr)]">
      <section aria-labelledby="probe-title" className="border bg-surface p-5"><h2 id="probe-title" className="text-xl font-semibold">Probe configuration</h2><p className="mt-1 text-sm leading-6 text-muted">Arguments are newline-delimited argv values. No shell command is constructed.</p><form onSubmit={runAnalysis} className="mt-5 grid gap-4"><Field label="Executable" error={fieldErrors.executable}><input value={probe.executable} onChange={(event) => setProbe({ ...probe, executable: event.target.value })} className={inputClass} /></Field><Field label="Arguments" hint="One exact argument per line." error={fieldErrors.argumentsText}><textarea value={probe.argumentsText} onChange={(event) => setProbe({ ...probe, argumentsText: event.target.value })} rows={5} className={`${inputClass} py-3 font-mono`} /></Field><Field label="Working directory" hint="Relative to the repository root." error={fieldErrors.workingDirectory}><input value={probe.workingDirectory} onChange={(event) => setProbe({ ...probe, workingDirectory: event.target.value })} className={`${inputClass} font-mono`} /></Field><div className="grid gap-4 sm:grid-cols-3"><Field label="Timeout (seconds)" error={fieldErrors.timeoutSeconds}><input type="number" min={1} max={3600} value={probe.timeoutSeconds} onChange={(event) => setProbe({ ...probe, timeoutSeconds: event.target.value })} className={inputClass} /></Field><Field label="stdout limit (bytes)" error={fieldErrors.maxStdoutBytes}><input type="number" min={1} max={1048576} value={probe.maxStdoutBytes} onChange={(event) => setProbe({ ...probe, maxStdoutBytes: event.target.value })} className={inputClass} /></Field><Field label="stderr limit (bytes)" error={fieldErrors.maxStderrBytes}><input type="number" min={1} max={1048576} value={probe.maxStderrBytes} onChange={(event) => setProbe({ ...probe, maxStderrBytes: event.target.value })} className={inputClass} /></Field></div><button disabled={busy || analyses.some((analysis) => shouldPollBisectionAnalysis(analysis.status))} className={primaryButtonClass}>{busy ? "Starting…" : "Create and start analysis"}</button></form></section>
      <section aria-labelledby="analysis-title" className="min-w-0"><div className="flex flex-wrap items-end justify-between gap-3"><div><h2 id="analysis-title" className="text-xl font-semibold">Analysis evidence</h2><p className="mt-1 text-sm text-muted">Polling runs every five seconds only while queued, running, or waiting.</p></div>{analyses.length > 0 && <label className="text-sm font-semibold">Analysis <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)} className={`ml-2 ${inputClass} w-auto`} aria-label="Selected bisection analysis">{analyses.map((analysis) => <option key={analysis.id} value={analysis.id}>{shortSha(analysis.id)} · {analysis.status}</option>)}</select></label>}</div>{selected ? <AnalysisEvidence analysis={selected} runs={runs} /> : <div className="mt-4"><ViewState title="No bisection analyses" detail="Configure the exact executable and argv probe to start the first analysis." /></div>}</section>
    </div>
  </div>;
}

function AnalysisEvidence({ analysis, runs }: { analysis: BisectionAnalysis; runs: ExecutionRun[] }) {
  const steps = sortAnalysisSteps(analysis.steps);
  const passCount = steps.filter((step) => step.observed_outcome === "pass").length;
  const regressionCount = steps.filter((step) => step.observed_outcome === "regression").length;
  const relatedRuns = runs.filter((run) => analysis.steps.some((step) => step.execution_run_id === run.id));
  return <div className="mt-4 space-y-5"><div className="flex flex-wrap items-center gap-3"><EvidenceBadge value={analysis.status} /><FullSha value={analysis.id} /></div><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"><Metric label="Known-good boundary" value={shortSha(analysis.good_commit_sha)} /><Metric label="Known-regressed boundary" value={shortSha(analysis.bad_commit_sha)} /><Metric label="Current interval" value={analysis.current_interval_size} /><Metric label="Persisted steps" value={analysis.step_count} detail={`${passCount} usable PASS · ${regressionCount} usable REGRESSION`} /><Metric label="Usable evidence" value={analysis.usable_evidence_count} detail={`${analysis.reused_evidence_count} reused · ${analysis.new_evidence_count} requested`} /><Metric label="Skipped evidence" value={analysis.indeterminate_count + analysis.execution_failure_count} detail={`${analysis.indeterminate_count} indeterminate · ${analysis.execution_failure_count} execution failures`} /></div>
    {analysis.status === "attributed" && <section aria-labelledby="attribution-title" className="border border-emerald-300 bg-emerald-50 p-5"><h3 id="attribution-title" className="font-semibold text-emerald-950">Observed regression boundary</h3><div className="mt-4 grid gap-4 sm:grid-cols-2"><Boundary title="Previous known-good commit" sha={analysis.final_good_commit_sha} snapshot={analysis.final_good_snapshot} /><Boundary title="First observed regressed commit" sha={analysis.final_bad_commit_sha} snapshot={analysis.final_bad_snapshot} /></div><dl className="mt-4 grid gap-3 border border-emerald-300 bg-white p-4 text-sm sm:grid-cols-2"><div><dt className="font-semibold">Probe executable</dt><dd className="mt-1 break-all font-mono text-xs">{analysis.configuration.executable}</dd></div><div><dt className="font-semibold">Working directory</dt><dd className="mt-1 break-all font-mono text-xs">{analysis.configuration.working_directory}</dd></div><div><dt className="font-semibold">Arguments</dt><dd className="mt-1 break-words font-mono text-xs">{analysis.configuration.args.length ? analysis.configuration.args.join(" · ") : "None"}</dd></div><div><dt className="font-semibold">Bisection steps</dt><dd className="mt-1 font-mono text-xs">{analysis.step_count}</dd></div></dl><p className="mt-4 text-sm leading-6 text-emerald-950">This identifies the first observed regression boundary within the frozen commit range under this configured probe. It does not by itself prove causal root cause.</p></section>}
    {analysis.status === "inconclusive" && <section className="border border-amber-300 bg-amber-50 p-5"><h3 className="font-semibold text-amber-950">Inconclusive</h3><p className="mt-2 text-sm text-amber-950">{terminalReasonLabel(analysis.terminal_reason)}{analysis.terminal_message ? ` — ${analysis.terminal_message}` : ""}</p></section>}
    {analysis.status === "failed" && <section className="border border-red-300 bg-red-50 p-5"><h3 className="font-semibold text-red-900">Analysis failed</h3><p className="mt-2 text-sm text-red-900">{terminalReasonLabel(analysis.terminal_reason)}{analysis.terminal_message ? ` — ${analysis.terminal_message}` : ""}</p></section>}
    <section aria-labelledby="timeline-title"><h3 id="timeline-title" className="font-semibold">Decision timeline</h3>{steps.length === 0 ? <p className="mt-2 text-sm text-muted">No persisted decisions yet.</p> : <ol className="mt-3 space-y-3">{steps.map((step) => <li key={step.sequence_number} className="border bg-surface p-4"><div className="flex flex-wrap items-center justify-between gap-2"><strong>Step {step.sequence_number}</strong><span className="font-mono text-xs">{evidenceSourceLabel(step.evidence_source)}</span></div><p className="mt-2 break-words text-sm">Selected <code title={step.selected_commit_sha}>{shortSha(step.selected_commit_sha)}</code> → <strong>{executionOutcomeLabel(step.observed_outcome)}</strong> → {step.decision.replaceAll("_", " ")}</p><p className="mt-2 break-all font-mono text-xs text-muted">Before {shortSha(step.good_commit_sha_before)}…{shortSha(step.bad_commit_sha_before)} · after {shortSha(step.good_commit_sha_after)}…{shortSha(step.bad_commit_sha_after)}</p></li>)}</ol>}</section>
    <ExecutionEvidence runs={relatedRuns} />
  </div>;
}

function Boundary({ title, sha, snapshot }: { title: string; sha: string | null; snapshot: Record<string, unknown> | null }) {
  const subject = snapshot?.subject; const committedAt = snapshot?.committed_at; const position = snapshot?.position;
  return <div><h4 className="text-xs font-semibold uppercase tracking-wide">{title}</h4><div className="mt-1">{sha ? <FullSha value={sha} /> : "Not available"}</div>{typeof subject === "string" && <p className="mt-1 break-words text-sm">{subject}</p>}<p className="mt-1 text-xs text-emerald-950">Position {typeof position === "number" ? position : "not available"}{typeof committedAt === "string" ? ` · ${committedAt}` : ""}</p></div>;
}

function ExecutionEvidence({ runs }: { runs: ExecutionRun[] }) {
  return <section aria-labelledby="execution-title"><h3 id="execution-title" className="font-semibold">Execution evidence</h3>{runs.length === 0 ? <p className="mt-2 text-sm text-muted">No execution evidence has been persisted for these decisions.</p> : <div className="mt-3 space-y-4">{runs.flatMap((run) => run.targets.map((target) => <article key={`${run.id}-${target.commit_sha}`} className="min-w-0 border bg-surface p-4"><div className="flex flex-wrap items-center justify-between gap-2"><FullSha value={target.commit_sha} /><strong className="text-xs">{target.outcome ? executionOutcomeLabel(target.outcome) : target.status.toUpperCase()}</strong></div><dl className="mt-3 grid gap-3 text-sm sm:grid-cols-3"><div><dt className="text-muted">Attempts</dt><dd>{target.attempt_count}</dd></div><div><dt className="text-muted">Exit code</dt><dd>{target.exit_code ?? "Not available"}</dd></div><div><dt className="text-muted">Duration</dt><dd>{target.duration_ms === null ? "Not available" : `${target.duration_ms} ms`}</dd></div></dl>{target.failure_kind && <p role="alert" className="mt-3 break-words text-sm text-red-900">{target.failure_kind}: {target.failure_message ?? "No failure message"}</p>}<div className="mt-3 grid gap-3 lg:grid-cols-2"><Output title={`stdout${target.stdout_truncated ? " (truncated)" : ""}`} value={target.stdout} /><Output title={`stderr${target.stderr_truncated ? " (truncated)" : ""}`} value={target.stderr} /></div></article>))}</div>}</section>;
}

function Output({ title, value }: { title: string; value: string }) {
  return <div className="min-w-0"><h4 className="text-xs font-semibold uppercase tracking-wide">{title}</h4><pre className="mt-1 max-h-52 overflow-auto whitespace-pre-wrap break-words border bg-white p-3 text-xs">{value || "No output"}</pre></div>;
}
