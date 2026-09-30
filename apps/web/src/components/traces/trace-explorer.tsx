"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { PageHeader } from "@/components/product-context";
import { listTraces, type TraceSummary } from "@/lib/trace-api";
import { appendUniqueTraces, parseTraceFilters, serializeTraceFilters, type TraceFilters as FilterValues } from "@/lib/trace-explorer";

import { TraceFilters } from "./trace-filters";
import { TraceList } from "./trace-list";

export function TraceExplorer() {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const rawSearch = searchParams.toString();
  const filters = useMemo(() => parseTraceFilters(new URLSearchParams(rawSearch)), [rawSearch]);
  const filterKey = serializeTraceFilters(filters).toString();
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const loadMoreController = useRef<AbortController | null>(null);

  useEffect(() => {
    loadMoreController.current?.abort();
    const controller = new AbortController();
    const requestFilters = parseTraceFilters(new URLSearchParams(filterKey));
    listTraces(requestFilters, { signal: controller.signal })
      .then((response) => {
        setTraces(response.items);
        setNextCursor(response.next_cursor);
        setHasMore(response.has_more);
        setLoadedKey(filterKey);
        setError(null);
        setLoadMoreError(null);
        setLoadingMore(false);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setLoadedKey(filterKey);
          setError(reason instanceof Error ? reason.message : "Unable to load traces.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [filterKey, retry]);

  const updateFilters = useCallback((values: FilterValues) => {
    const query = serializeTraceFilters(values).toString();
    router.push(query ? `${pathname}?${query}` : pathname);
  }, [pathname, router]);

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    const controller = new AbortController();
    loadMoreController.current = controller;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      const response = await listTraces(filters, { cursor: nextCursor, signal: controller.signal });
      setTraces((current) => appendUniqueTraces(current, response.items));
      setNextCursor(response.next_cursor);
      setHasMore(response.has_more);
    } catch (reason) {
      if (!controller.signal.aborted) setLoadMoreError(reason instanceof Error ? reason.message : "Unable to load more traces.");
    } finally {
      if (!controller.signal.aborted) setLoadingMore(false);
    }
  }

  const hasFilters = Object.keys(filters).length > 0;
  const isCurrent = loadedKey === filterKey;
  const isLoading = loading || !isCurrent;
  const currentError = isCurrent ? error : null;
  const currentTraces = isCurrent ? traces : [];

  return (
    <div className="mx-auto max-w-[90rem]">
      <PageHeader eyebrow="OBSERVE / TRACES" title="Trace Explorer" description="Inspect agent executions, failures, latency, token use, and span structure from the live query API." actions={!isLoading && !currentError && <p className="font-mono text-xs text-muted" role="status">{currentTraces.length} loaded{hasMore ? " · more available" : ""}</p>} />

      <section aria-labelledby="filters-title" className="mt-6">
        <h2 id="filters-title" className="sr-only">Trace filters</h2>
        <TraceFilters key={filterKey} filters={filters} onApply={updateFilters} onClear={() => updateFilters({})} />
        {hasFilters && (
          <div className="mt-3 flex flex-wrap gap-2" aria-label="Active filters">
            {Object.entries(filters).map(([key, value]) => (
              <span key={key} className="border bg-accent-soft px-2 py-1 font-mono text-xs text-accent">{key.replaceAll("_", " ")}: {String(value)}</span>
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="results-title" className="mt-6">
        <div className="mb-3 flex items-center justify-between">
          <h2 id="results-title" className="text-lg font-semibold">Executions</h2>
          <span className="text-xs text-muted">Newest first · browser-local time</span>
        </div>
        {isLoading ? <TraceSkeleton /> : currentError ? (
          <State title="Couldn’t load traces" detail={currentError} role="alert">
            <button onClick={() => { setLoading(true); setError(null); setRetry((value) => value + 1); }} className="mt-4 min-h-11 border bg-white px-4 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-accent">Retry</button>
          </State>
        ) : currentTraces.length === 0 ? (
          <State
            title={hasFilters ? "No traces match these filters" : "No traces ingested yet"}
            detail={hasFilters ? "Adjust or clear the active filters to widen the search." : "Send traces through POST /api/v1/traces, then return here to inspect them."}
          >
            {hasFilters && <button onClick={() => updateFilters({})} className="mt-4 min-h-11 border bg-white px-4 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-accent">Clear filters</button>}
          </State>
        ) : (
          <TraceList traces={currentTraces} explorerQuery={filterKey} />
        )}

        {!isLoading && !currentError && currentTraces.length > 0 && hasMore && (
          <div className="mt-4 border bg-surface p-4 text-center">
            {loadMoreError && <p role="alert" className="mb-3 text-sm text-red-800">{loadMoreError} Existing results are preserved.</p>}
            <button onClick={loadMore} disabled={loadingMore} className="min-h-11 border bg-white px-5 text-sm font-semibold disabled:cursor-wait disabled:opacity-60 focus-visible:ring-2 focus-visible:ring-accent">
              {loadingMore ? "Loading…" : loadMoreError ? "Retry load more" : "Load more"}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}

function TraceSkeleton() {
  return <div aria-busy="true" aria-label="Loading traces" className="space-y-1">{Array.from({ length: 5 }, (_, index) => <div key={index} className="h-28 animate-pulse border bg-surface sm:h-24" />)}</div>;
}

function State({ title, detail, role = "status", children }: { title: string; detail: string; role?: "status" | "alert"; children?: React.ReactNode }) {
  return <div role={role} className="border bg-surface px-6 py-12 text-center"><h3 className="text-lg font-semibold">{title}</h3><p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-muted">{detail}</p>{children}</div>;
}
