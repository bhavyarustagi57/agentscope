# Trace ingestion contract

## Endpoint

`POST /api/v1/traces` accepts `Content-Type: application/json` and the frozen schema-1 envelope:

```text
EnvelopeV1 {
  schema_version: "1"
  traces: TraceV1[1..10]
}

TraceV1 {
  trace_id: string[1..128]
  name: string[1..500]
  start_time: timezone-aware RFC 3339 timestamp
  end_time: timezone-aware RFC 3339 timestamp | null
  status: "unset" | "running" | "success" | "error"
  input, output: JSON value
  error: {type: string[1..200], message: string[0..4000]} | null
  metadata: JSON object
  tags: string[0..200][0..100]
  spans: SpanV1[0..10000]
  duration_ms: finite non-negative number | null
}

SpanV1 {
  span_id, trace_id: string[1..128]
  parent_span_id: string[1..128] | null
  name: string[1..500]
  kind: "agent" | "llm" | "tool" | "retrieval" | "workflow" | "custom"
  start_time, end_time, status, input, output, error, duration_ms: as on TraceV1
  metadata, attributes: JSON object
  events: {name, timestamp, attributes}[]
  llm: LLMAttributesV1 | null
}

LLMAttributesV1 {
  provider, model, operation, finish_reason: string | null
  token_usage: {input_tokens, output_tokens, total_tokens} | null
  tool_calls: {id, name, arguments, result}[0..1000]
  temperature: finite number | null
  attributes: JSON object
}
```

All objects reject unknown fields. Token counts are non-negative; when input and output counts are
both present, total must equal their sum. Timestamps must be timezone-aware, end cannot precede start,
and duration requires an end time. JSON values are limited to depth 64 and 100,000 nodes. The total
request body limit is 4 MiB.

Every span must carry its enclosing trace ID. Span IDs are unique across a batch. A parent must exist
in the same submitted trace; self-parenting and cycles are rejected. Multiple roots and child-before-
parent ordering are supported. Span time boundaries receive one second of clock-skew tolerance around
the enclosing trace; duration is not required to equal wall-clock elapsed time exactly.

## Responses

- All new: `202 {"accepted":2,"duplicates":0}`
- Exact retry: `202 {"accepted":0,"duplicates":2}`
- Mixed: `202 {"accepted":1,"duplicates":1}`
- Same trace ID, different content: `409` with error code `TRACE_CONFLICT`
- Malformed or invalid request: `422` with `VALIDATION_ERROR`
- Unknown schema version: `422` with `UNSUPPORTED_SCHEMA_VERSION`
- Body over 4 MiB: `413` with `REQUEST_TOO_LARGE`
- Safe unexpected database failure: `500` with `PERSISTENCE_ERROR`

Errors use `{"error":{"code":"...","message":"...","details":...}}`; details appear only for
boundary validation and never contain SQL, credentials, stack traces, or connection strings.

## Atomicity and idempotency

The entire accepted request is one database transaction. Validation finishes before persistence and
any conflict or insert failure rolls back all new traces and spans in that batch. No per-span commit
occurs.

Canonicalization parses schema-1 values, emits their JSON representation with sorted object keys and
compact separators, preserves list order, and hashes the UTF-8 bytes with SHA-256. The hash is stored
with the trace. PostgreSQL's trace primary key and `INSERT ... ON CONFLICT DO NOTHING` choose the sole
winner under concurrent delivery. The server then compares fingerprints: equality is an exact
idempotent retry, inequality is a conflict and never overwrites stored data.

`Idempotency-Key` is optional visible ASCII up to 255 characters. The SDK uses the trace ID for one
trace and a stable digest of ordered trace IDs for a batch. The server validates the header for future
middleware compatibility but deliberately does not make it a second source of persistence identity.

## Storage and privacy

`traces` holds the canonical trace ID, query fields, JSONB trace data, fingerprint, and ingestion time.
`spans` holds the canonical span ID, trace/parent IDs, query fields, and JSONB event/LLM/extension data.
Primary, unique, foreign-key, same-trace-parent, self-parent, duration, and timestamp constraints keep
cheap integrity guarantees in PostgreSQL. Explorer-oriented indexes cover trace name/status/start and
span kind/status/start/parent traversal.

The endpoint is unauthenticated for loopback-bound local deployments. Optional Bearer headers are accepted by
the HTTP stack but not authenticated yet. Do not expose it publicly. Logs contain outcome category,
counts, and processing duration only—not payloads, prompts, responses, metadata, bearer values, or
idempotency keys.

Evaluation, production authentication, tenancy, rate limiting, retention, and durable client spooling
are deferred. The query APIs and Trace Explorer consume this stored schema-1 representation without
mutating it.
