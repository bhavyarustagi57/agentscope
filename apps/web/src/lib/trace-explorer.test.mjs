import assert from "node:assert/strict";
import test from "node:test";

import {
  appendUniqueTraces,
  buildTraceDetailHref,
  buildTraceExplorerHref,
  buildTraceQuery,
  buildSpanTree,
  formatDuration,
  formatTimestamp,
  parseTraceFilters,
} from "./trace-explorer.ts";

test("trace detail links preserve the complete supported explorer query", () => {
  const detailHref = buildTraceDetailHref(
    "trace/with spaces",
    "name=research+%26+tools&status=error&has_error=true&span_kind=llm&started_after=2026-09-17T06%3A00%3A00.000Z&started_before=2026-09-18T06%3A00%3A00.000Z&min_duration_ms=3000&max_duration_ms=9000",
  );

  assert.equal(
    detailHref,
    "/traces/trace%2Fwith%20spaces?from=status%3Derror%26name%3Dresearch%2B%2526%2Btools%26has_error%3Dtrue%26span_kind%3Dllm%26started_after%3D2026-09-17T06%253A00%253A00.000Z%26started_before%3D2026-09-18T06%253A00%253A00.000Z%26min_duration_ms%3D3000%26max_duration_ms%3D9000",
  );
});

test("trace explorer return state is internal, filtered, and safe by default", () => {
  assert.equal(buildTraceExplorerHref(undefined), "/traces");
  assert.equal(buildTraceExplorerHref(["status=error"]), "/traces");
  assert.equal(buildTraceExplorerHref("https://evil.example/steal"), "/traces");
  assert.equal(
    buildTraceExplorerHref("status=error&span_kind=tool&unsupported=ignored"),
    "/traces?status=error&span_kind=tool",
  );
});

test("buildTraceQuery serializes only active filters and the opaque cursor", () => {
  const query = buildTraceQuery(
    {
      status: "error",
      name: "research & tools",
      has_error: true,
      span_kind: "llm",
      started_after: "2026-09-17T06:00:00.000Z",
      min_duration_ms: 250,
    },
    "opaque_cursor",
  );

  assert.equal(
    query,
    "status=error&name=research+%26+tools&has_error=true&span_kind=llm&started_after=2026-09-17T06%3A00%3A00.000Z&min_duration_ms=250&page_size=20&cursor=opaque_cursor",
  );
});

test("parseTraceFilters ignores unsupported and invalid URL values", () => {
  const filters = parseTraceFilters(
    new URLSearchParams(
      "status=imaginary&span_kind=tool&has_error=false&min_duration_ms=-1&max_duration_ms=1250&name=+agent+",
    ),
  );

  assert.deepEqual(filters, {
    name: "agent",
    has_error: false,
    span_kind: "tool",
    max_duration_ms: 1250,
  });
});

test("formatDuration uses compact human units", () => {
  assert.equal(formatDuration(null), "—");
  assert.equal(formatDuration(820), "820 ms");
  assert.equal(formatDuration(1_000), "1.00 s");
  assert.equal(formatDuration(1420), "1.42 s");
  assert.equal(formatDuration(59_999), "1m 0s");
  assert.equal(formatDuration(60_000), "1m 0s");
  assert.equal(formatDuration(134000), "2m 14s");
  assert.equal(formatDuration(119600), "2m 0s");
});

test("formatTimestamp is explicit and stable when a timezone is supplied", () => {
  assert.match(formatTimestamp("2026-09-17T06:00:00Z", "UTC"), /17 Sep 2026/);
  assert.match(formatTimestamp("2026-09-17T06:00:00Z", "UTC"), /UTC/);
  assert.equal(
    formatTimestamp("2026-09-17T11:30:00+05:30", "UTC"),
    formatTimestamp("2026-09-17T06:00:00Z", "UTC"),
  );
  assert.equal(formatTimestamp("not-a-date", "UTC"), "—");
});

test("buildSpanTree preserves multiple roots and nested children", () => {
  const [first, second] = buildSpanTree([
    { span_id: "root-a", parent_span_id: null },
    { span_id: "child", parent_span_id: "root-a" },
    { span_id: "root-b", parent_span_id: null },
  ]);

  assert.equal(first.span.span_id, "root-a");
  assert.equal(first.children[0].span.span_id, "child");
  assert.equal(second.span.span_id, "root-b");
});

test("buildSpanTree promotes malformed orphan and cycle nodes without looping", () => {
  const roots = buildSpanTree([
    { span_id: "orphan", parent_span_id: "missing" },
    { span_id: "cycle-a", parent_span_id: "cycle-b" },
    { span_id: "cycle-b", parent_span_id: "cycle-a" },
  ]);

  assert.deepEqual(
    roots.map((node) => node.span.span_id),
    ["orphan", "cycle-a", "cycle-b"],
  );
  assert.ok(roots.every((node) => node.malformed));
});

test("buildSpanTree handles the maximum-depth trace without quadratic work", () => {
  const spans = Array.from({ length: 10_000 }, (_, index) => ({
    span_id: `span-${index}`,
    parent_span_id: index === 0 ? null : `span-${index - 1}`,
  }));

  const started = performance.now();
  const roots = buildSpanTree(spans);

  assert.equal(roots.length, 1);
  assert.ok(performance.now() - started < 5_000);
});

test("appendUniqueTraces keeps existing order and removes page overlap", () => {
  const traces = appendUniqueTraces(
    [{ trace_id: "a" }, { trace_id: "b" }],
    [{ trace_id: "b" }, { trace_id: "c" }],
  );

  assert.deepEqual(traces.map((trace) => trace.trace_id), ["a", "b", "c"]);
});
