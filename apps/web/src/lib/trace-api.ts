import type { SpanKind, TraceFilters, TraceStatus } from "./trace-explorer";
import { buildTraceQuery } from "./trace-explorer";
import { apiBaseUrl } from "./api-base-url.ts";

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export type TraceSummary = {
  trace_id: string;
  name: string;
  status: TraceStatus;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  ingested_at: string;
  tags: string[];
  output_available: boolean;
  span_count: number;
  error_span_count: number;
  llm_span_count: number;
  tool_span_count: number;
  total_input_tokens: number | null;
  total_output_tokens: number | null;
  total_tokens: number | null;
};

export type TraceError = { type: string; message: string };
export type TokenUsage = {
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
};
export type ToolCall = { id: string | null; name: string; arguments: JsonValue; result: JsonValue };
export type LlmAttributes = {
  provider: string | null;
  model: string | null;
  operation: string | null;
  token_usage: TokenUsage | null;
  finish_reason: string | null;
  tool_calls: ToolCall[];
  temperature: number | null;
  attributes: Record<string, JsonValue>;
};
export type SpanEvent = {
  name: string;
  timestamp: string;
  attributes: Record<string, JsonValue>;
};
export type SpanDetail = {
  span_id: string;
  trace_id: string;
  parent_span_id: string | null;
  name: string;
  kind: SpanKind;
  status: TraceStatus;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  input: JsonValue;
  output: JsonValue;
  error: TraceError | null;
  metadata: Record<string, JsonValue>;
  attributes: Record<string, JsonValue>;
  events: SpanEvent[];
  llm: LlmAttributes | null;
};
export type TraceDetail = Omit<TraceSummary, "span_count" | "error_span_count" | "llm_span_count" | "tool_span_count" | "total_input_tokens" | "total_output_tokens" | "total_tokens"> & {
  input: JsonValue;
  output: JsonValue;
  error: TraceError | null;
  metadata: Record<string, JsonValue>;
  spans: SpanDetail[];
};
export type TraceListResponse = {
  items: TraceSummary[];
  next_cursor: string | null;
  has_more: boolean;
};

export class TraceApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "TraceApiError";
  }
}

export async function listTraces(
  filters: TraceFilters,
  options: { cursor?: string; signal?: AbortSignal } = {},
): Promise<TraceListResponse> {
  const data = await request(`/api/v1/traces?${buildTraceQuery(filters, options.cursor)}`, options.signal);
  const record = expectRecord(data);
  if (!Array.isArray(record.items) || typeof record.has_more !== "boolean") invalidResponse();
  if (record.next_cursor !== null && typeof record.next_cursor !== "string") invalidResponse();
  return {
    items: record.items.map(parseSummary),
    next_cursor: record.next_cursor as string | null,
    has_more: record.has_more,
  };
}

export async function getTrace(traceId: string, signal?: AbortSignal): Promise<TraceDetail> {
  const data = await request(`/api/v1/traces/${encodeURIComponent(traceId)}`, signal);
  const record = expectRecord(data);
  const summary = parseSummary({
    ...record,
    output_available: record.output !== null,
    span_count: 0,
    error_span_count: 0,
    llm_span_count: 0,
    tool_span_count: 0,
    total_input_tokens: 0,
    total_output_tokens: 0,
    total_tokens: 0,
  });
  if (!Array.isArray(record.spans)) invalidResponse();
  return {
    ...summary,
    input: record.input as JsonValue,
    output: record.output as JsonValue,
    error: parseError(record.error),
    metadata: expectJsonRecord(record.metadata),
    spans: record.spans.map(parseSpan),
  };
}

async function request(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(`${apiBaseUrl}${path}`, { signal, headers: { Accept: "application/json" } });
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new TraceApiError("The trace service returned an unreadable response.", response.status);
  }
  if (!response.ok) {
    const error = readApiError(data);
    throw new TraceApiError(error.message, response.status, error.code);
  }
  return data;
}

function parseSummary(value: unknown): TraceSummary {
  const item = expectRecord(value);
  const status = expectEnum(item.status, ["unset", "running", "success", "error"] as const);
  return {
    trace_id: expectString(item.trace_id),
    name: expectString(item.name),
    status,
    started_at: expectString(item.started_at),
    ended_at: expectNullableString(item.ended_at),
    duration_ms: expectNullableNumber(item.duration_ms),
    ingested_at: expectString(item.ingested_at),
    tags: expectStringArray(item.tags),
    output_available: expectBoolean(item.output_available),
    span_count: expectNumber(item.span_count),
    error_span_count: expectNumber(item.error_span_count),
    llm_span_count: expectNumber(item.llm_span_count),
    tool_span_count: expectNumber(item.tool_span_count),
    total_input_tokens: expectNullableNumber(item.total_input_tokens),
    total_output_tokens: expectNullableNumber(item.total_output_tokens),
    total_tokens: expectNullableNumber(item.total_tokens),
  };
}

function parseSpan(value: unknown): SpanDetail {
  const span = expectRecord(value);
  return {
    span_id: expectString(span.span_id),
    trace_id: expectString(span.trace_id),
    parent_span_id: expectNullableString(span.parent_span_id),
    name: expectString(span.name),
    kind: expectEnum(span.kind, ["agent", "llm", "tool", "retrieval", "workflow", "custom"] as const),
    status: expectEnum(span.status, ["unset", "running", "success", "error"] as const),
    started_at: expectString(span.started_at),
    ended_at: expectNullableString(span.ended_at),
    duration_ms: expectNullableNumber(span.duration_ms),
    input: span.input as JsonValue,
    output: span.output as JsonValue,
    error: parseError(span.error),
    metadata: expectJsonRecord(span.metadata),
    attributes: expectJsonRecord(span.attributes),
    events: expectArray(span.events).map((event) => {
      const item = expectRecord(event);
      return {
        name: expectString(item.name),
        timestamp: expectString(item.timestamp),
        attributes: expectJsonRecord(item.attributes),
      };
    }),
    llm: span.llm === null ? null : parseLlm(span.llm),
  };
}

function parseLlm(value: unknown): LlmAttributes {
  const llm = expectRecord(value);
  const usage = llm.token_usage === null ? null : expectRecord(llm.token_usage);
  return {
    provider: expectNullableString(llm.provider),
    model: expectNullableString(llm.model),
    operation: expectNullableString(llm.operation),
    token_usage: usage
      ? {
          input_tokens: expectNullableNumber(usage.input_tokens),
          output_tokens: expectNullableNumber(usage.output_tokens),
          total_tokens: expectNullableNumber(usage.total_tokens),
        }
      : null,
    finish_reason: expectNullableString(llm.finish_reason),
    tool_calls: expectArray(llm.tool_calls).map((call) => {
      const item = expectRecord(call);
      return {
        id: expectNullableString(item.id),
        name: expectString(item.name),
        arguments: item.arguments as JsonValue,
        result: item.result as JsonValue,
      };
    }),
    temperature: expectNullableNumber(llm.temperature),
    attributes: expectJsonRecord(llm.attributes),
  };
}

function readApiError(value: unknown): { code?: string; message: string } {
  if (isRecord(value) && isRecord(value.error)) {
    return {
      code: typeof value.error.code === "string" ? value.error.code : undefined,
      message: typeof value.error.message === "string" ? value.error.message : "Trace request failed.",
    };
  }
  return { message: "Trace request failed." };
}

function parseError(value: unknown): TraceError | null {
  if (value === null) return null;
  const error = expectRecord(value);
  return { type: expectString(error.type), message: expectString(error.message) };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function expectRecord(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) invalidResponse();
  return value;
}
function expectJsonRecord(value: unknown): Record<string, JsonValue> {
  return expectRecord(value) as Record<string, JsonValue>;
}
function expectArray(value: unknown): unknown[] {
  if (!Array.isArray(value)) invalidResponse();
  return value;
}
function expectString(value: unknown): string {
  if (typeof value !== "string") invalidResponse();
  return value;
}
function expectNullableString(value: unknown): string | null {
  if (value !== null && typeof value !== "string") invalidResponse();
  return value;
}
function expectNumber(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) invalidResponse();
  return value;
}
function expectBoolean(value: unknown): boolean {
  if (typeof value !== "boolean") invalidResponse();
  return value;
}
function expectNullableNumber(value: unknown): number | null {
  if (value !== null && (typeof value !== "number" || !Number.isFinite(value))) invalidResponse();
  return value;
}
function expectStringArray(value: unknown): string[] {
  const items = expectArray(value);
  if (!items.every((item) => typeof item === "string")) invalidResponse();
  return items as string[];
}
function expectEnum<T extends string>(value: unknown, allowed: readonly T[]): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) invalidResponse();
  return value as T;
}
function invalidResponse(): never {
  throw new TraceApiError("The trace service returned an unexpected response.", 502, "INVALID_RESPONSE");
}
