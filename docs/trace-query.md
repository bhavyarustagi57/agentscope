# Trace query API

## List traces

`GET /api/v1/traces` returns summaries ordered by `started_at DESC, trace_id DESC`.

| Parameter | Meaning |
| --- | --- |
| `page_size` | 1–100; defaults to 50 |
| `cursor` | Opaque continuation cursor, maximum 1,024 characters |
| `status` | Exact schema-1 trace status |
| `name` | Case-insensitive literal substring, 1–200 characters |
| `started_after`, `started_before` | Inclusive, timezone-aware timestamp bounds |
| `min_duration_ms`, `max_duration_ms` | Inclusive, non-negative duration bounds |
| `has_error` | Trace status is error or at least one span status is error |
| `span_kind` | Trace contains at least one span of the schema-1 kind |

Contradictory ranges return `422 VALIDATION_ERROR`. Name search escapes `%` and `_`; it is literal
substring matching, not indexed fuzzy or full-text search.

The response is `{"items":[...],"next_cursor":"..."|null,"has_more":bool}`. Each summary contains
trace identity, status, timestamps, duration, ingestion time, tags, span/error/LLM/tool counts,
separate input/output/total token sums, and a derived `output_available` boolean without exposing the
output. The flag means stored output is non-null; explicit JSON null and uncaptured output remain
indistinguishable. PostgreSQL computes all aggregates in the list query; the API
does not load spans or issue one query per trace. A token sum is `null` when no provider reported that
measurement and `0` only when a provider explicitly reported zero, preserving unknown-versus-zero
semantics.

The cursor contains only the last immutable `(started_at, trace_id)` key, a cursor version, and a
SHA-256 fingerprint of normalized filters. It is URL-safe base64 JSON, not an authentication token.
Malformed cursors return `400 INVALID_CURSOR`; reuse with different filters returns
`400 CURSOR_FILTER_MISMATCH`. Page size is excluded from the fingerprint so it may change within the
documented bound while continuing the same filtered traversal.

Pagination is deterministic but is not a cross-request database snapshot. A trace newer than the
cursor boundary and ingested after the first page is not inserted into that in-progress traversal;
a newly ingested trace older than the boundary can appear on a later page. Consumers should restart
from the first page when they need a fresh view.

## Get one trace

`GET /api/v1/traces/{trace_id}` returns the complete stored public trace representation, including
trace input/output/error/metadata/tags and every span's hierarchy, timing, input/output/error,
metadata, attributes, events, and LLM/tool-call data. `ingested_at` is useful observability metadata;
the internal fingerprint is not exposed.

Spans are flat with `parent_span_id`. Roots and siblings are ordered by `(started_at, span_id)`; an
linear iterative traversal guarantees parents precede children even when children arrived first or
timestamps tie. Unknown IDs return `404 TRACE_NOT_FOUND`. Detail is complete rather than silently
truncated; the 10,000-span ingestion limit is its primary response bound.

## Architecture and limitations

The flow is FastAPI route → typed schemas → query service → async SQLAlchemy → PostgreSQL. Keyset
pagination avoids offset drift. Composite `(started_at, trace_id)` and `(trace_id, kind)` indexes
support paging and span-kind existence checks. Substring name filtering may scan candidates until
search requirements justify specialized indexing.

The endpoints remain unauthenticated local-development APIs. Logs contain safe counts, categories,
and duration only—never payloads, metadata, attributes, tool data, authorization values, or cursors.
Production auth, tenancy, rate limiting, retention/deletion, evaluation, experiments, and regression
analysis remain unimplemented. The current API is intended for trusted local development only.
