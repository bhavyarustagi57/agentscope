"use client";

import { useState } from "react";

import {
  SPAN_KINDS,
  TRACE_STATUSES,
  toDateTimeLocal,
  type TraceFilters as FilterValues,
} from "@/lib/trace-explorer";

export function TraceFilters({
  filters,
  onApply,
  onClear,
}: {
  filters: FilterValues;
  onApply: (filters: FilterValues) => void;
  onClear: () => void;
}) {
  const [draft, setDraft] = useState(filters);

  return (
    <form
      className="border bg-surface p-4"
      onSubmit={(event) => {
        event.preventDefault();
        onApply(draft);
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <label className="grid gap-1.5 text-sm font-semibold">
          Name contains
          <input
            type="search"
            value={draft.name ?? ""}
            maxLength={200}
            placeholder="Literal substring"
            onChange={(event) => setDraft({ ...draft, name: event.target.value || undefined })}
            className="min-h-11 border bg-white px-3 font-normal outline-none focus-visible:ring-2 focus-visible:ring-accent"
          />
        </label>
        <label className="grid gap-1.5 text-sm font-semibold">
          Status
          <select
            value={draft.status ?? ""}
            onChange={(event) =>
              setDraft({ ...draft, status: (event.target.value || undefined) as FilterValues["status"] })
            }
            className="min-h-11 border bg-white px-3 font-normal outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <option value="">Any status</option>
            {TRACE_STATUSES.map((status) => (
              <option key={status} value={status}>{status}</option>
            ))}
          </select>
        </label>
        <label className="grid gap-1.5 text-sm font-semibold">
          Error state
          <select
            value={draft.has_error === undefined ? "" : String(draft.has_error)}
            onChange={(event) =>
              setDraft({
                ...draft,
                has_error: event.target.value === "" ? undefined : event.target.value === "true",
              })
            }
            className="min-h-11 border bg-white px-3 font-normal outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <option value="">Any</option>
            <option value="true">Has errors</option>
            <option value="false">No errors</option>
          </select>
        </label>
        <label className="grid gap-1.5 text-sm font-semibold">
          Contains span kind
          <select
            value={draft.span_kind ?? ""}
            onChange={(event) =>
              setDraft({ ...draft, span_kind: (event.target.value || undefined) as FilterValues["span_kind"] })
            }
            className="min-h-11 border bg-white px-3 font-normal outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <option value="">Any kind</option>
            {SPAN_KINDS.map((kind) => (
              <option key={kind} value={kind}>{kind}</option>
            ))}
          </select>
        </label>
      </div>

      <details className="mt-4 border-t pt-3">
        <summary className="cursor-pointer text-sm font-semibold text-muted focus-visible:outline-2 focus-visible:outline-accent">
          Time and duration filters
        </summary>
        <div className="mt-3 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <DateFilter label="Started after" value={draft.started_after} onChange={(value) => setDraft({ ...draft, started_after: value })} />
          <DateFilter label="Started before" value={draft.started_before} onChange={(value) => setDraft({ ...draft, started_before: value })} />
          <NumberFilter label="Minimum duration (ms)" value={draft.min_duration_ms} onChange={(value) => setDraft({ ...draft, min_duration_ms: value })} />
          <NumberFilter label="Maximum duration (ms)" value={draft.max_duration_ms} onChange={(value) => setDraft({ ...draft, max_duration_ms: value })} />
        </div>
      </details>

      <div className="mt-4 flex flex-wrap items-center gap-3 border-t pt-4">
        <button type="submit" className="min-h-11 bg-panel px-5 text-sm font-semibold text-white outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2">
          Apply filters
        </button>
        <button type="button" onClick={onClear} className="min-h-11 border bg-white px-5 text-sm font-semibold outline-none hover:bg-canvas focus-visible:ring-2 focus-visible:ring-accent">
          Clear filters
        </button>
        <p className="text-xs text-muted">Search runs only when you apply the form.</p>
      </div>
    </form>
  );
}

function DateFilter({ label, value, onChange }: { label: string; value?: string; onChange: (value?: string) => void }) {
  return (
    <label className="grid gap-1.5 text-sm font-semibold">
      {label}
      <input
        type="datetime-local"
        value={toDateTimeLocal(value)}
        onChange={(event) => onChange(event.target.value ? new Date(event.target.value).toISOString() : undefined)}
        className="min-h-11 min-w-0 border bg-white px-3 font-normal outline-none focus-visible:ring-2 focus-visible:ring-accent"
      />
    </label>
  );
}

function NumberFilter({ label, value, onChange }: { label: string; value?: number; onChange: (value?: number) => void }) {
  return (
    <label className="grid gap-1.5 text-sm font-semibold">
      {label}
      <input
        type="number"
        min="0"
        step="any"
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value === "" ? undefined : Number(event.target.value))}
        className="min-h-11 border bg-white px-3 font-normal outline-none focus-visible:ring-2 focus-visible:ring-accent"
      />
    </label>
  );
}
