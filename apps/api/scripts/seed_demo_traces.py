"""Seed realistic local Trace Explorer data through the public ingestion API."""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def make_span(
    trace_id: str,
    suffix: str,
    name: str,
    kind: str,
    start: datetime,
    duration_ms: int,
    *,
    parent: str | None = None,
    status: str = "success",
    input_value: Any = None,
    output_value: Any = None,
    error: dict[str, str] | None = None,
    attributes: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
    llm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    span_id = f"{trace_id}-{suffix}"
    return {
        "span_id": span_id,
        "trace_id": trace_id,
        "parent_span_id": f"{trace_id}-{parent}" if parent else None,
        "name": name,
        "kind": kind,
        "start_time": iso(start),
        "end_time": iso(start + timedelta(milliseconds=duration_ms)),
        "status": status,
        "input": input_value,
        "output": output_value,
        "error": error,
        "metadata": {"fixture": "phase-3-prompt-3"},
        "attributes": attributes or {},
        "events": events or [],
        "llm": llm,
        "duration_ms": duration_ms,
    }


def llm_data(index: int, *, tools: bool = False) -> dict[str, Any]:
    input_tokens = 110 + index
    output_tokens = 42 + index
    return {
        "provider": "openai",
        "model": "gpt-demo-1",
        "operation": "chat.completions.create",
        "token_usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        "finish_reason": "tool_calls" if tools else "stop",
        "tool_calls": [
            {
                "id": f"call_{index}",
                "name": "search_docs",
                "arguments": {"query": "agent reliability"},
                "result": {"hits": 3},
            }
        ]
        if tools
        else [],
        "temperature": 0.2,
        "attributes": {"response_id": f"resp_demo_{index}", "seed": index},
    }


def make_trace(run_id: str, index: int, now: datetime) -> dict[str, Any]:
    trace_id = f"demo-p3-{run_id}-{index:02d}"
    start = now - timedelta(minutes=index)
    variant = index % 4

    if variant == 0:
        name, duration, status = f"Research workflow {index:02d}", 2_420, "success"
        spans = [
            make_span(trace_id, "root", "Research workflow", "workflow", start, duration),
            make_span(
                trace_id,
                "plan",
                "Plan research",
                "llm",
                start + timedelta(milliseconds=80),
                620,
                parent="root",
                llm=llm_data(index, tools=True),
            ),
            make_span(
                trace_id,
                "search",
                "Search documentation",
                "tool",
                start + timedelta(milliseconds=760),
                540,
                parent="root",
                input_value={"query": "agent reliability"},
                output_value={"documents": ["design.md", "runbook.md", "tests.md"]},
                attributes={"tool.name": "search_docs"},
                events=[
                    {
                        "name": "results.received",
                        "timestamp": iso(start + timedelta(milliseconds=1_250)),
                        "attributes": {"count": 3},
                    }
                ],
            ),
            make_span(
                trace_id,
                "answer",
                "Synthesize answer",
                "llm",
                start + timedelta(milliseconds=1_360),
                910,
                parent="root",
                llm=llm_data(index + 1),
            ),
        ]
    elif variant == 1:
        name, duration, status = f"Failed tool workflow {index:02d}", 1_860, "error"
        spans = [
            make_span(
                trace_id,
                "root",
                "Customer lookup",
                "workflow",
                start,
                duration,
                status="error",
                error={"type": "ToolExecutionError", "message": "Customer database lookup failed"},
            ),
            make_span(
                trace_id,
                "agent",
                "Lookup agent",
                "agent",
                start + timedelta(milliseconds=70),
                1_620,
                parent="root",
                status="error",
                error={
                    "type": "ToolExecutionError",
                    "message": "The lookup tool returned a bounded timeout",
                },
            ),
            make_span(
                trace_id,
                "tool",
                "lookup_customer",
                "tool",
                start + timedelta(milliseconds=340),
                1_120,
                parent="agent",
                status="error",
                input_value={"customer_id": f"cust-{index:03d}"},
                output_value=None,
                error={"type": "TimeoutError", "message": "Database request exceeded 1 second"},
                attributes={"tool.name": "lookup_customer", "retryable": True},
                events=[
                    {
                        "name": "retry.exhausted",
                        "timestamp": iso(start + timedelta(milliseconds=1_430)),
                        "attributes": {"attempts": 2},
                    }
                ],
            ),
        ]
    elif variant == 2:
        name, duration, status = f"Single LLM request {index:02d}", 740, "success"
        spans = [
            make_span(
                trace_id,
                "llm",
                "Generate concise answer",
                "llm",
                start,
                duration,
                input_value={"messages": 2},
                output_value={"answer": "A concise fixture response."},
                llm=llm_data(index),
            )
        ]
    else:
        name, duration, status = f"Nested graph run {index:02d}", 3_260, "success"
        spans = [
            make_span(trace_id, "root", "Graph invocation", "workflow", start, duration),
            make_span(
                trace_id,
                "node-a",
                "Retrieve context",
                "custom",
                start + timedelta(milliseconds=100),
                1_050,
                parent="root",
                attributes={"graph.node": "retrieve"},
            ),
            make_span(
                trace_id,
                "tool",
                "vector_search",
                "tool",
                start + timedelta(milliseconds=210),
                610,
                parent="node-a",
                input_value={"top_k": 4},
                output_value={"matches": 4},
            ),
            make_span(
                trace_id,
                "node-b",
                "Draft response",
                "custom",
                start + timedelta(milliseconds=1_220),
                1_760,
                parent="root",
                attributes={"graph.node": "draft"},
            ),
            make_span(
                trace_id,
                "llm",
                "Draft with context",
                "llm",
                start + timedelta(milliseconds=1_350),
                1_310,
                parent="node-b",
                llm=llm_data(index),
            ),
            make_span(
                trace_id,
                "audit",
                "Independent audit root",
                "custom",
                start + timedelta(milliseconds=3_020),
                160,
                events=[
                    {
                        "name": "audit.completed",
                        "timestamp": iso(start + timedelta(milliseconds=3_150)),
                        "attributes": {"policy": "demo"},
                    }
                ],
            ),
        ]

    return {
        "trace_id": trace_id,
        "name": name,
        "start_time": iso(start),
        "end_time": iso(start + timedelta(milliseconds=duration)),
        "status": status,
        "input": {"request_id": f"demo-{index:02d}", "scenario": variant},
        "output": None if status == "error" else {"completed": True},
        "error": {"type": "WorkflowError", "message": "A nested tool failed"}
        if status == "error"
        else None,
        "metadata": {"environment": "local-demo", "fixture_version": 1},
        "tags": ["demo", "phase-3", ["research", "failure", "llm", "graph"][variant]],
        "spans": spans,
        "duration_ms": duration,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--count", type=int, default=28)
    args = parser.parse_args()
    if not 1 <= args.count <= 100:
        parser.error("--count must be between 1 and 100")

    now = datetime.now(UTC).replace(microsecond=0)
    run_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    traces = [make_trace(run_id, index, now) for index in range(args.count)]
    endpoint = f"{args.api_url.rstrip('/')}/api/v1/traces"

    accepted = duplicates = 0
    for offset in range(0, len(traces), 10):
        body = json.dumps({"schema_version": "1", "traces": traces[offset : offset + 10]}).encode()
        request = urllib.request.Request(
            endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise SystemExit(f"ingestion failed ({error.code}): {detail}") from error
        accepted += result["accepted"]
        duplicates += result["duplicates"]

    print(f"Seeded {accepted} traces ({duplicates} duplicates); run prefix demo-p3-{run_id}")


if __name__ == "__main__":
    main()
