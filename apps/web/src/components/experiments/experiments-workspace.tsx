"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { PageHeader } from "@/components/product-context";

import { createExperiment, EXPERIMENT_STATUSES, listExperiments, type ExperimentStatus, type ExperimentSummary } from "@/lib/experiment-api";
import { buildExperimentHref, buildExperimentListHref, parseBoundedOffset } from "@/lib/experiment-ui";
import { formatTimestamp } from "@/lib/trace-explorer";

import { StatusBadge, ViewState, buttonClass, inputClass, primaryButtonClass } from "./shared";

export function ExperimentsWorkspace() {
  const router = useRouter(); const search = useSearchParams();
  const rawStatus = search.get("status"); const status = EXPERIMENT_STATUSES.includes(rawStatus as ExperimentStatus) ? rawStatus as ExperimentStatus : undefined;
  const offset = parseBoundedOffset(search.get("offset"));
  const [items, setItems] = useState<ExperimentSummary[]>([]); const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true); const [error, setError] = useState<string | null>(null); const [retry, setRetry] = useState(0);
  const [name, setName] = useState(""); const [description, setDescription] = useState(""); const [creating, setCreating] = useState(false); const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => { const controller = new AbortController();
    listExperiments({ status, offset, pageSize: 20, signal: controller.signal }).then((page) => { setItems(page.items); setHasMore(page.has_more); setError(null); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load experiments."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort();
  }, [offset, retry, status]);

  async function submit(event: React.FormEvent) { event.preventDefault(); const cleanName = name.trim(); const cleanDescription = description.trim();
    if (!cleanName || cleanName.length > 200 || cleanDescription.length > 2_000) { setFormError("Enter a name up to 200 characters and a description up to 2,000 characters."); return; }
    setCreating(true); setFormError(null); try { const created = await createExperiment({ name: cleanName, description: cleanDescription || null }); router.push(buildExperimentHref(created.id)); }
    catch (reason) { setFormError(reason instanceof Error ? reason.message : "Unable to create experiment."); setCreating(false); }
  }

  return <div className="mx-auto max-w-[90rem]">
    <PageHeader eyebrow="EVALUATE / EXPERIMENTS" title="A/B experiments" description="Pair distinct A and B trace executions, freeze their provenance and evaluation conditions, then inspect durable results and per-condition statistical evidence." />
    <div className="mt-7 grid gap-6 xl:grid-cols-[22rem_minmax(0,1fr)]">
      <form onSubmit={submit} className="border bg-surface p-5"><h2 className="font-semibold">Create a draft</h2><p className="mt-1 text-sm leading-6 text-muted">Configuration remains editable until you explicitly mark the experiment ready.</p>
        <label className="mt-5 grid gap-1.5 text-sm font-semibold">Name <span className="text-xs font-normal text-muted">Required</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} className={inputClass} /></label>
        <label className="mt-4 grid gap-1.5 text-sm font-semibold">Description<textarea value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2000} rows={4} className={`${inputClass} py-3`} /></label>
        {formError && <p role="alert" className="mt-4 text-sm text-red-800">{formError}</p>}<button disabled={creating} className={`mt-5 w-full ${primaryButtonClass}`}>{creating ? "Creating…" : "Create experiment"}</button>
      </form>
      <section aria-labelledby="experiment-list-title"><div className="flex flex-wrap items-end justify-between gap-3"><div><h2 id="experiment-list-title" className="font-semibold">Experiment records</h2><p className="mt-1 text-sm text-muted">Bounded to 20 records per page.</p></div><label className="grid gap-1 text-xs font-semibold">Status<select value={status ?? ""} onChange={(e) => router.push(buildExperimentListHref(e.target.value, 0))} className={inputClass}><option value="">All statuses</option>{EXPERIMENT_STATUSES.map((value) => <option key={value} value={value}>{value}</option>)}</select></label></div>
        {loading ? <div aria-busy="true" aria-label="Loading experiments" className="mt-4 h-80 animate-pulse border bg-surface" /> : error ? <div className="mt-4"><ViewState title="Couldn’t load experiments" detail={error} role="alert"><button onClick={() => { setLoading(true); setRetry((value) => value + 1); }} className={`mt-4 ${primaryButtonClass}`}>Retry</button></ViewState></div> : items.length === 0 ? <div className="mt-4"><ViewState title="No experiments found" detail={status ? "No experiments match this lifecycle status." : "Create a draft to begin a controlled paired comparison."} /></div> : <ul className="mt-4 space-y-3">{items.map((item) => <li key={item.id} className="border bg-surface p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><Link href={buildExperimentHref(item.id)} className="font-semibold text-accent underline-offset-4 hover:underline">{item.name}</Link><p className="mt-1 line-clamp-2 text-sm text-muted">{item.description || "No description"}</p></div><StatusBadge status={item.status} /></div><dl className="mt-4 grid grid-cols-3 gap-3 border-t pt-3 text-sm"><Count label="Paired subjects" value={item.subject_count} /><Count label="Conditions" value={item.evaluation_condition_count} /><Count label="Updated" value={formatTimestamp(item.updated_at)} /></dl></li>)}</ul>}
        {!loading && !error && (offset > 0 || hasMore) && <nav aria-label="Experiment pages" className="mt-4 flex justify-between"><Link aria-disabled={offset === 0} href={buildExperimentListHref(status ?? "", Math.max(0, offset - 20))} className={`${buttonClass} ${offset === 0 ? "pointer-events-none opacity-50" : ""}`}>Previous</Link><Link aria-disabled={!hasMore} href={buildExperimentListHref(status ?? "", offset + 20)} className={`${buttonClass} ${!hasMore ? "pointer-events-none opacity-50" : ""}`}>Next</Link></nav>}
      </section>
    </div>
  </div>;
}

function Count({ label, value }: { label: string; value: string | number }) { return <div><dt className="text-xs font-semibold text-muted">{label}</dt><dd className="mt-1 break-words font-mono text-xs">{value}</dd></div>; }
