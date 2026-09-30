# AgentScope Python SDK

`agentscope-tracing` instruments Python AI-agent applications and produces vendor-neutral,
JSON-compatible traces. Its core has no runtime dependencies. Traces can remain in memory, be queued
for delivery to a compatible HTTP endpoint, or be populated by optional explicit OpenAI and
LangGraph adapters.

## Installation

```text
pip install agentscope-tracing
pip install "agentscope-tracing[openai]"
pip install "agentscope-tracing[langgraph]"
pip install "agentscope-tracing[openai,langgraph]"
```

The core install does not contain or import OpenAI, LangGraph, or LangChain. The LangGraph adapter is
imported explicitly from `agentscope_sdk.adapters.langgraph` after installing its extra. The lock
currently selects OpenAI 2.54.0 and LangGraph 1.2.11; package constraints allow compatible OpenAI
2.x and LangGraph 1.2+ releases below their next major versions.

## Core concepts

- `Tracer` creates traces and spans and owns context-local nesting.
- `Trace` represents one logical agent execution.
- `Span` represents an agent, LLM, tool, retrieval, workflow, or custom operation.
- `TraceExporter` receives a completed trace. `InMemoryTraceExporter` is provided for local use and
  tests.
- `HttpTraceExporter` serializes, batches, and delivers traces on one bounded background thread.

## Basic usage

```python
from agentscope_sdk import InMemoryTraceExporter, SpanKind, Tracer

exporter = InMemoryTraceExporter()
tracer = Tracer(exporter=exporter)

with tracer.trace("research-agent", input={"topic": "observability"}) as trace:
    with trace.span("plan", kind=SpanKind.AGENT):
        with trace.span("draft", kind=SpanKind.LLM) as completion:
            completion.set_output({"content": "Use structured traces."})
    trace.set_output({"status": "done"})

print(exporter.traces[0].to_json(indent=2))
```

The same objects support `async with`. Python `contextvars` isolate active traces and spans between
asyncio tasks, while nested spans automatically use the active span in the same trace as their
parent.

## Lifecycle and exceptions

Entering a trace or span activates it. A normal exit records `success`; an exceptional exit records
the exception type and message as structured data, finalizes timing, restores context, and re-raises
the original exception. End timestamps are assigned once, and attempts to reuse a completed context
raise `RuntimeError`.

## Serialization

`Trace.to_json()` emits UTF-8 JSON with lowercase enum values and UTC RFC 3339 timestamps ending in
`Z`. `Trace.from_json()` restores the typed model. Values in input, output, metadata, attributes,
events, and tool-call arguments must already be JSON-compatible. Recursive structures, naive
timestamps, non-finite floats, non-string mapping keys, arbitrary objects, and payloads above the
default 1 MiB limit fail with `TraceSerializationError`; objects are never converted with `repr()`.
The SDK retains caller-provided data rather than silently mutating or redacting it.

Applications are responsible for deciding what sensitive data may be traced. Do not place secrets,
credentials, or regulated data in telemetry without an application-level policy.

## OpenAI Chat Completions adapter

Raw tracing remains provider-neutral. Install the optional OpenAI client only when needed:

```text
pip install "agentscope-tracing[openai]"
```

`OpenAIInstrumentation` explicitly calls the modern sync or async
`client.chat.completions.create(...)` API; it does not patch clients or process-global state. The
adapter is tested with OpenAI Python 2.x (the lock currently selects 2.54.0). It does not support the
Responses API, legacy Completions, streaming, or automatic tool execution.

```python
from openai import OpenAI
from agentscope_sdk import OpenAICapture, OpenAIInstrumentation, SpanKind, Tracer

tracer = Tracer()
instrumentation = OpenAIInstrumentation(tracer, capture=OpenAICapture.METADATA)
client = OpenAI()  # Credentials remain inside the OpenAI client and are never traced.

with tracer.trace("assistant") as trace:
    with trace.span("agent-loop", kind=SpanKind.AGENT):
        response = instrumentation.chat_completions(
            client,
            model="your-model",
            messages=[{"role": "user", "content": "Hello"}],
        )
```

For `AsyncOpenAI`, use `await instrumentation.achat_completions(...)` inside an active async trace.
Both helpers require an active trace, inherit the active agent/workflow span as their parent, and
return the provider response unchanged. Provider exceptions mark the LLM span as failed, restore the
previous context, and propagate as the original exception. Input/response normalization failures do
not replace a successful provider result; they add a sanitized `instrumentation.error` span event.

### LLM mapping and capture policy

Each call creates an `openai.chat.completions` LLM span. `LLMAttributes` records provider `OpenAI`,
operation `chat.completions`, the returned model (falling back to the requested model), requested
model, response ID, temperature, prompt/completion/total tokens, first-choice finish reason, and
function calls. Function arguments are parsed as JSON when valid and otherwise retained as strings.
Messages and function calls are normalized field-by-field into AgentScope-owned JSON values; the
adapter never serializes a client, arbitrary provider object, `__dict__`, or `repr()`.

`OpenAICapture.METADATA` is the conservative default and stores neither prompts nor model output.
`INPUTS`, `OUTPUTS`, and `ALL` opt into normalized messages, response messages, or both. Captured
message fields include role, content, name, tool-call ID, and function calls. Inputs and outputs may
contain sensitive or regulated data; enable them only with an application-level data policy. API
keys, authorization headers, client configuration, and environment values are never inspected.
Existing trace depth/type checks and the 1 MiB serialized payload limit still apply.

Streaming requests fail deterministically with `NotImplementedError` before the provider is called;
consume no stream through this adapter. Tool execution stays explicit using the existing
`with trace.span(name, kind=SpanKind.TOOL, input=...)` API, which already records nesting, timing,
output, and exceptions without introducing a tool framework.

Run the deterministic two-LLM/one-tool example without a network connection or API key:

```text
npm run example:sdk:openai
```

Replace its fake client with `OpenAI` or `AsyncOpenAI` for a real application. The trace can use
`InMemoryTraceExporter` or `HttpTraceExporter`; server ingestion is not implemented in this
repository, so configuring the latter requires another compatible endpoint.

## LangGraph adapter

`LangGraphInstrumentation` uses LangGraph's documented per-invocation `RunnableConfig` callback
surface. It does not patch LangGraph, LangChain, compiled graphs, or process-global state. Existing
callbacks in the invocation config are merged and preserved.

```python
from langgraph.graph import END, START, StateGraph

from agentscope_sdk import InMemoryTraceExporter, Tracer
from agentscope_sdk.adapters.langgraph import LangGraphInstrumentation

exporter = InMemoryTraceExporter()
tracer = Tracer(exporter=exporter)
instrumentation = LangGraphInstrumentation(tracer)

builder = StateGraph(dict)
builder.add_node("answer", lambda state: {**state, "answer": "done"})
builder.add_edge(START, "answer")
builder.add_edge("answer", END)

result = instrumentation.invoke(builder.compile(), {"question": "hello"}, name="demo")
```

With no active AgentScope trace, `invoke` or `ainvoke` owns exactly one trace for the invocation. In
an existing trace it adds one `WORKFLOW` span and exports no additional trace. Actual LangGraph graph
steps become `CUSTOM` spans because node names do not safely imply agent or tool semantics. Subgraph
steps nest beneath their containing node. Instrumented OpenAI calls and explicit generic `TOOL`
spans created inside a node inherit that node as parent; the LangGraph adapter does not synthesize
or duplicate LLM/tool details.

`LangGraphCapture.METADATA` is the default and stores no graph or node state. `INPUTS`, `OUTPUTS`, and
`ALL` opt into graph and node input/output capture. Capture copies only strict JSON values (null,
booleans, finite numbers, strings, lists, and string-keyed objects). It never uses arbitrary object
`repr()`, and a normalization failure adds a sanitized `instrumentation.error` event without changing
the graph result. Applications remain responsible for excluding credentials, personal data, and
regulated content.

Provider/node exceptions keep their original identity, mark the node, workflow, and owned trace as
errors, finalize timing, restore context, and propagate. Sync thread-parallel nodes, async parallel
nodes, concurrent graph invocations, existing-trace ownership, and nested subgraphs are covered by
deterministic tests.

Supported through this adapter: `invoke` and `ainvoke` on local compiled graphs. `stream` and
`astream` fail explicitly with `NotImplementedError`. Checkpoint storage itself remains LangGraph's
responsibility; interrupt/resume sessions and one-trace aggregation across multiple invocations are
not supported. Remote graphs and checkpoint/replay semantics are not verified in Phase 2.

Run the offline cross-adapter example (workflow → node → LLM/tool spans) with no API key or network:

```text
npm run example:sdk:langgraph
```

The integration follows LangGraph's public [graph invocation and node metadata
contract](https://docs.langchain.com/oss/python/langgraph/graph-api#nodes) and the public
[`Pregel` runnable API](https://reference.langchain.com/python/langgraph/pregel/main/Pregel).

## HTTP exporter

```python
import os

from agentscope_sdk import HttpTraceExporter, RetryPolicy, Tracer

exporter = HttpTraceExporter(
    endpoint="https://agentscope.example.test/api/v1/traces",
    api_token=os.environ.get("AGENTSCOPE_API_TOKEN"),
    retry_policy=RetryPolicy(max_attempts=3),
    batch_size=10,
)
tracer = Tracer(exporter=exporter)

with tracer.trace("research-agent"):
    pass

exporter.close(timeout=5)
```

The endpoint is required and must be an absolute HTTP or HTTPS URL without embedded credentials,
query parameters, or fragments. Supplying a token requires HTTPS. The optional token becomes an
`Authorization: Bearer ...` header; it is never placed in trace JSON, failure records, or exporter
representations. Custom headers are validated, reject line breaks, and cannot override
`Authorization`, `Content-Type`, `Content-Length`, `Host`, `Accept`, or `Idempotency-Key`.

Environment loading is the application's responsibility. The SDK does not read environment files or
provide a secrets manager.

### Wire contract

- Method: `POST`
- URL: the exact configured endpoint; `/api/v1/traces` is the documented future AgentScope path
- Content type and accepted response type: `application/json`
- Body: `{"schema_version":"1","traces":[<trace>, ...]}`
- Success: any `2xx`; an optional response body must be a JSON object
- Retryable: connection failures, timeouts, `429`, `500`, `502`, `503`, and `504`
- Non-retryable: other HTTP statuses, serialization failures, and malformed success responses

The standard-library transport does not follow redirects. A single-trace request uses its immutable
`trace_id` as `Idempotency-Key`. A batch uses `batch_` plus SHA-256 of its ordered trace IDs. Retries
reuse the exact request body and key. A future server must deduplicate individual records by
`trace_id` and may additionally deduplicate whole requests by `Idempotency-Key`; delivery is
not exactly-once. Retries can duplicate an accepted request, while process termination can still
lose memory-only work.

The request-level schema version defines the complete nested trace-record shape; traces do not carry
a second redundant version field. Phase 3 must validate version `"1"`, preserve ordered batch records,
deduplicate each immutable `trace_id`, treat the idempotency key as the request identity, and return a
JSON object or an empty body on any `2xx`. Malformed/authentication requests should use non-retryable
`4xx` responses; transient throttling/unavailability should use `429`, `500`, `502`, `503`, or `504`
and may provide `Retry-After`. Authentication, when configured, is `Authorization: Bearer <token>`;
tokens must not appear in URLs or response bodies.

### Frozen trace record (schema `1`)

Every record contains `trace_id`, `name`, UTC `start_time`/`end_time`, explicit `status`, optional
`input`/`output`/structured `error`, extensible `metadata`, ordered `tags`, flat ordered `spans`, and
`duration_ms`. Every span contains stable `span_id`, matching `trace_id`, optional `parent_span_id`,
`name`, stable `kind`, timestamps/status/data/error, metadata/attributes/events, optional structured
LLM data, and duration. IDs are generated once in the client. Parent links reconstruct the tree;
UTC RFC 3339 timestamps provide wall-clock ordering and monotonic durations provide elapsed time.
`SpanKind` and field names are schema contracts for Phase 3, not display labels to infer.

### Retries and failures

`RetryPolicy` defaults to three total attempts with bounded exponential backoff and jitter.
`Retry-After` seconds or HTTP dates are respected up to `max_delay`. Unit tests inject sleeping and
never require real delays.

HTTP delivery does not raise into the instrumented application. Sanitized failures are retained in
the bounded `exporter.failures` tuple and may also be observed with `on_error`. Categories distinguish
serialization, transport, HTTP, response-protocol, queue-capacity, payload-size, and closed-exporter
failures. A callback exception is contained; the original failure remains inspectable. Once retries
are exhausted, that in-memory batch is discarded—there is no durable spool yet. `on_error` may run
on the caller or delivery thread and therefore must be thread-safe and non-blocking.

### Batching and lifecycle

`export()` performs bounded serialization and enqueueing only; network calls and backoff run on one
daemon worker thread. The queue is bounded by trace count and serialized bytes. A batch flushes at
`batch_size`, after `flush_interval`, on `flush()`, or during `close()`. The 1 MiB per-trace boundary
remains in force, and `max_batch_bytes` independently caps each request without truncation.

`flush(timeout)` and `close(timeout)` return `False` rather than waiting forever. `close()` stops new
work, requests a final flush, and is idempotent. Exports after closing produce an observable `closed`
failure. Explicit lifecycle management is required; the SDK does not rely on `__del__`.

Trace completion remains non-blocking in asyncio applications because it never performs network I/O.
Call blocking lifecycle waits through `await asyncio.to_thread(exporter.flush, timeout)` and
`await asyncio.to_thread(exporter.close, timeout)` during async shutdown.

## Current limitations

The repository does not yet provide the server-side ingestion endpoint, authentication system,
persistence, or server deduplication. The client has no durable spool, cross-process queue, delivery
receipt store, automatic redaction, OpenTelemetry support, OpenAI Responses API, OpenAI streaming,
LangGraph streaming/interrupt-session aggregation, global monkey-patching, or evaluation behavior.
Phase 3 begins server ingestion and Trace Explorer work; neither is implemented here.
