export const TRACE_STATUSES = ["unset", "running", "success", "error"] as const;
export const SPAN_KINDS = ["agent", "llm", "tool", "retrieval", "workflow", "custom"] as const;

export type TraceStatus = (typeof TRACE_STATUSES)[number];
export type SpanKind = (typeof SPAN_KINDS)[number];

export type TraceFilters = {
  status?: TraceStatus;
  name?: string;
  has_error?: boolean;
  span_kind?: SpanKind;
  started_after?: string;
  started_before?: string;
  min_duration_ms?: number;
  max_duration_ms?: number;
};

type Identified = { trace_id: string };
type SpanLike = { span_id: string; parent_span_id: string | null };

export type SpanTreeNode<T extends SpanLike> = {
  span: T;
  children: SpanTreeNode<T>[];
  malformed: boolean;
};

const statusSet = new Set<string>(TRACE_STATUSES);
const kindSet = new Set<string>(SPAN_KINDS);

export function parseTraceFilters(params: URLSearchParams): TraceFilters {
  const filters: TraceFilters = {};
  const status = params.get("status");
  const name = params.get("name")?.trim();
  const hasError = params.get("has_error");
  const kind = params.get("span_kind");

  if (status && statusSet.has(status)) filters.status = status as TraceStatus;
  if (name && name.length <= 200) filters.name = name;
  if (hasError === "true" || hasError === "false") filters.has_error = hasError === "true";
  if (kind && kindSet.has(kind)) filters.span_kind = kind as SpanKind;

  for (const key of ["started_after", "started_before"] as const) {
    const value = params.get(key);
    if (value && !Number.isNaN(Date.parse(value))) filters[key] = new Date(value).toISOString();
  }
  for (const key of ["min_duration_ms", "max_duration_ms"] as const) {
    const value = params.get(key);
    if (value !== null) {
      const number = Number(value);
      if (Number.isFinite(number) && number >= 0) filters[key] = number;
    }
  }
  if (
    filters.started_after &&
    filters.started_before &&
    filters.started_after > filters.started_before
  ) {
    delete filters.started_before;
  }
  if (
    filters.min_duration_ms !== undefined &&
    filters.max_duration_ms !== undefined &&
    filters.min_duration_ms > filters.max_duration_ms
  ) {
    delete filters.max_duration_ms;
  }
  return filters;
}

export function serializeTraceFilters(filters: TraceFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return params;
}

function normalizeTraceFilterQuery(rawQuery: string): string {
  return serializeTraceFilters(parseTraceFilters(new URLSearchParams(rawQuery))).toString();
}

export function buildTraceDetailHref(traceId: string, explorerQuery: string): string {
  const path = `/traces/${encodeURIComponent(traceId)}`;
  const query = normalizeTraceFilterQuery(explorerQuery);
  return query ? `${path}?${new URLSearchParams({ from: query })}` : path;
}

export function buildTraceExplorerHref(returnState: string | string[] | undefined): string {
  if (typeof returnState !== "string") return "/traces";
  const query = normalizeTraceFilterQuery(returnState);
  return query ? `/traces?${query}` : "/traces";
}

export function buildTraceQuery(filters: TraceFilters, cursor?: string): string {
  const params = serializeTraceFilters(filters);
  params.set("page_size", "20");
  if (cursor) params.set("cursor", cursor);
  return params.toString();
}

export function appendUniqueTraces<T extends Identified>(current: T[], incoming: T[]): T[] {
  const seen = new Set(current.map((trace) => trace.trace_id));
  return [...current, ...incoming.filter((trace) => !seen.has(trace.trace_id))];
}

export function buildSpanTree<T extends SpanLike>(spans: T[]): SpanTreeNode<T>[] {
  const nodes = new Map<string, SpanTreeNode<T>>();
  for (const span of spans) {
    if (!nodes.has(span.span_id)) {
      nodes.set(span.span_id, { span, children: [], malformed: false });
    }
  }

  const relation = new Map<string, "valid" | "orphan" | "cycle">();
  for (const startId of nodes.keys()) {
    if (relation.has(startId)) continue;
    const path: string[] = [];
    const positions = new Set<string>();
    let currentId = startId;
    let reachesCycle = false;
    while (true) {
      const known = relation.get(currentId);
      if (known) {
        reachesCycle = known === "cycle";
        break;
      }
      if (positions.has(currentId)) {
        reachesCycle = true;
        break;
      }
      positions.add(currentId);
      path.push(currentId);
      const parentId = nodes.get(currentId)?.span.parent_span_id;
      if (!parentId) break;
      if (!nodes.has(parentId)) {
        relation.set(currentId, "orphan");
        path.pop();
        break;
      }
      currentId = parentId;
    }
    for (const id of path) relation.set(id, reachesCycle ? "cycle" : "valid");
  }

  const roots: SpanTreeNode<T>[] = [];
  for (const node of nodes.values()) {
    const parentId = node.span.parent_span_id;
    if (!parentId || relation.get(node.span.span_id) !== "valid") {
      node.malformed = Boolean(parentId);
      roots.push(node);
    } else {
      nodes.get(parentId)?.children.push(node);
    }
  }

  return roots;
}

export function formatDuration(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  if (value < 1_000) return `${Math.round(value)} ms`;
  if (value < 60_000) {
    const seconds = (value / 1_000).toFixed(value < 10_000 ? 2 : 1);
    return Number(seconds) < 60 ? `${seconds} s` : "1m 0s";
  }
  const seconds = Math.round(value / 1_000);
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function formatTimestamp(value: string | null, timeZone?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "—";
  const parts = new Intl.DateTimeFormat("en-US", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone,
    timeZoneName: "short",
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value ?? "";
  return `${part("day")} ${part("month")} ${part("year")}, ${part("hour")}:${part("minute")}:${part("second")} ${part("timeZoneName")}`;
}

export function toDateTimeLocal(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  const local = new Date(date.valueOf() - date.getTimezoneOffset() * 60_000);
  return Number.isNaN(local.valueOf()) ? "" : local.toISOString().slice(0, 16);
}
