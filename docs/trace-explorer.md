# Trace Explorer

## Routes and behavior

- `/traces` lists real `GET /api/v1/traces` summaries newest first.
- `/traces/[traceId]` loads `GET /api/v1/traces/{trace_id}` only when a trace is opened, so links,
  refresh, and browser navigation work normally.
- Status, literal name substring, error presence, span kind, timestamp, and duration filters are
  serialized into shareable URL search parameters. Unsupported URL values are ignored. Applying or
  clearing filters resets cursor state.
- “Load more” sends the opaque `next_cursor` unchanged, appends unique trace IDs, and preserves
  existing rows if the request fails. There are no artificial page numbers or total-page claims.

List rows use the aggregate counts and token totals already computed by PostgreSQL. Unknown token
usage is shown as `—`; a provider-reported zero remains `0`. The frontend does not fetch spans per
row. Initial loading, empty database, filtered empty, API failure, load-more failure, and detail 404
each have a distinct recovery-oriented state.

## Detail and hierarchy

Detail headers show the public trace identity, status, timing, ingestion time, tags, span-kind/error
counts, and captured token total. The flat `spans` response is reconstructed from `parent_span_id` in
input order with a linear iterative algorithm. Multiple roots are supported; missing parents and
cycles are promoted as visibly marked roots so malformed data cannot cause an infinite loop or
JavaScript call-stack overflow.

Root spans start expanded. Child branches use labelled keyboard-accessible expand/collapse buttons.
Selecting a span opens a responsive inspector with general timing and identity plus only the data
that exists: structured errors, LLM fields/token usage/tool calls, tool input/output, events,
attributes, and metadata. JSON is rendered with escaped `JSON.stringify` output in bounded scrolling
`pre` elements; trace data is never interpreted as HTML.

Timestamps are labelled as browser-local time and retain the exact ISO value in `title`. Durations use
milliseconds, seconds, or minute/second formatting without excessive precision. Desktop uses a
tree/inspector split; narrower viewports stack them and contain long identifiers and JSON.

## Local demo data

With the Compose stack running, seed 28 traces in API-sized batches through the real schema-1
ingestion route:

```powershell
uv run --project apps/api python apps/api/scripts/seed_demo_traces.py
```

`--count` accepts 1–100 and `--api-url` defaults to `http://localhost:8000`. The fixture includes
successful research workflows, failed nested tool calls with structured errors, single-LLM traces,
and nested graph-like traces with multiple roots, events, token usage, attributes, and metadata.
Trace IDs begin with `demo-p3-`; production startup never seeds data.
Each invocation creates a new timestamped run prefix, so repeated invocations intentionally add
another demo set. The script prints the exact prefix for selective cleanup.

Remove only the demo traces:

```powershell
docker compose exec -T postgres psql -U agentscope -d agentscope -c "DELETE FROM traces WHERE trace_id LIKE 'demo-p3-%';"
```

To remove one printed run instead, replace the pattern with its exact
`demo-p3-<run-id>-%` prefix. Span rows are removed by the trace foreign-key cascade.

## Architecture and limitations

```text
Browser
  ↓
Next.js Trace Explorer
  ↓
typed API client
  ↓
FastAPI query API
  ↓
TraceQueryService
  ↓
PostgreSQL
```

The UI has no third-party analytics, session replay, external fonts, state framework, or JSON editor.
Captured values stay on the local AgentScope boundary and render as ordinary React text. Production
authentication and tenancy, live trace streaming, and retention/deletion policy remain outside the
current local product boundary. Evaluation, experiments, regressions, and monitoring link back to the
canonical trace detail rather than duplicating trace payloads.
