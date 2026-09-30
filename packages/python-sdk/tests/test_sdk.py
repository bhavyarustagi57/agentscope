import asyncio
from datetime import datetime

import pytest

from agentscope_sdk import (
    ExecutionStatus,
    InMemoryTraceExporter,
    LLMAttributes,
    SpanKind,
    TokenUsage,
    ToolCall,
    Trace,
    Tracer,
    TraceSerializationError,
)


def test_trace_and_span_success_lifecycle_and_nesting() -> None:
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)

    with tracer.trace("research-agent", input={"topic": "testing"}) as trace:
        assert trace.status is ExecutionStatus.RUNNING
        assert trace.start_time.tzinfo is not None

        with trace.span("plan", kind=SpanKind.AGENT) as parent:
            with tracer.span("model", kind=SpanKind.LLM) as child:
                child.set_output({"answer": "plan"})
            with trace.span("search", kind=SpanKind.TOOL) as sibling:
                sibling.set_output(["result"])

        trace.set_output({"answer": "done"})

    assert trace.status is ExecutionStatus.SUCCESS
    assert trace.end_time is not None
    assert trace.duration_ms is not None and trace.duration_ms >= 0
    assert trace.output == {"answer": "done"}
    assert len({span.span_id for span in trace.spans}) == 3
    assert parent.parent_span_id is None
    assert child.parent_span_id == parent.span_id
    assert sibling.parent_span_id == parent.span_id
    assert all(span.trace_id == trace.trace_id for span in trace.spans)
    assert all(span.status is ExecutionStatus.SUCCESS for span in trace.spans)
    assert all(span.end_time is not None for span in trace.spans)
    assert exporter.traces == (trace,)
    assert tracer.current_trace is None
    assert tracer.current_span is None


def test_ids_are_unique() -> None:
    tracer = Tracer()

    with tracer.trace("first") as first:
        with first.span("one") as first_span:
            pass
        with first.span("two") as second_span:
            pass
    with tracer.trace("second") as second:
        pass

    assert first.trace_id != second.trace_id
    assert first_span.span_id != second_span.span_id


def test_exceptions_are_recorded_reraised_and_context_is_restored() -> None:
    tracer = Tracer()

    with tracer.trace("outer") as outer:
        with pytest.raises(ValueError, match="bad tool input") as raised:
            with outer.span("tool", kind=SpanKind.TOOL) as failed_span:
                raise ValueError("bad tool input")

        assert tracer.current_trace is outer
        assert tracer.current_span is None
        assert failed_span.status is ExecutionStatus.ERROR
        assert failed_span.error is not None
        assert failed_span.error.type == "ValueError"
        assert failed_span.error.message == "bad tool input"
        assert raised.value is not None

    with pytest.raises(RuntimeError, match="agent failed") as raised_trace:
        with tracer.trace("failure") as failed_trace:
            raise RuntimeError("agent failed")

    assert failed_trace.status is ExecutionStatus.ERROR
    assert failed_trace.error is not None
    assert failed_trace.error.type == "RuntimeError"
    assert failed_trace.end_time is not None
    assert raised_trace.value is not None
    assert tracer.current_trace is None
    assert tracer.current_span is None


def test_nested_trace_is_independent_and_restores_outer_context() -> None:
    tracer = Tracer()

    with tracer.trace("outer") as outer:
        with outer.span("outer-span") as outer_span:
            with tracer.trace("inner") as inner:
                assert tracer.current_trace is inner
                assert tracer.current_span is None
                with inner.span("inner-span") as inner_span:
                    assert inner_span.parent_span_id is None
            assert tracer.current_trace is outer
            assert tracer.current_span is outer_span


def test_export_failure_does_not_replace_application_exception() -> None:
    application_error = KeyError("original")

    class FailingExporter:
        def export(self, trace: Trace) -> None:
            raise RuntimeError(f"cannot export {trace.trace_id}")

    tracer = Tracer(exporter=FailingExporter())

    with pytest.raises(KeyError) as raised:
        with tracer.trace("failure"):
            raise application_error

    assert raised.value is application_error
    assert any("trace export failed" in note for note in raised.value.__notes__)
    assert tracer.current_trace is None


def test_serialization_round_trip_preserves_typed_llm_data() -> None:
    tracer = Tracer()
    llm = LLMAttributes(
        provider="openai-compatible",
        model="example-model",
        operation="chat",
        token_usage=TokenUsage(input_tokens=8, output_tokens=5, total_tokens=13),
        finish_reason="stop",
        temperature=0.2,
        tool_calls=(ToolCall(name="lookup", arguments={"query": "AgentScope"}),),
        attributes={"vendor.request_id": "request-1"},
    )

    with tracer.trace(
        "serialization",
        input={"messages": ["hello"]},
        metadata={"environment": "test"},
        tags=("sdk", "round-trip"),
    ) as trace:
        with trace.span("completion", kind=SpanKind.LLM, llm=llm) as span:
            span.add_event("first-token", attributes={"sequence": 1})
            span.set_output({"content": "hi"})

    restored = Trace.from_json(trace.to_json())

    assert restored.to_dict() == trace.to_dict()
    assert restored.spans[0].kind is SpanKind.LLM
    assert restored.spans[0].llm == llm
    assert restored.start_time.tzinfo is not None
    assert restored.end_time is not None and restored.end_time.tzinfo is not None


@pytest.mark.parametrize(
    "value",
    [object(), {"bad": object()}, {"nested": [object()]}],
)
def test_serialization_rejects_arbitrary_objects_without_using_repr(value: object) -> None:
    tracer = Tracer()
    with tracer.trace("unsafe", input=value) as trace:
        pass

    with pytest.raises(TraceSerializationError, match="JSON-compatible"):
        trace.to_json()


def test_serialization_rejects_cycles_naive_timestamps_and_oversized_payloads() -> None:
    recursive: list[object] = []
    recursive.append(recursive)
    tracer = Tracer()

    with tracer.trace("recursive", input=recursive) as recursive_trace:
        pass
    with pytest.raises(TraceSerializationError, match="recursive"):
        recursive_trace.to_json()

    with tracer.trace("naive") as naive_trace:
        pass
    naive_trace.start_time = datetime(2026, 1, 1)
    with pytest.raises(TraceSerializationError, match="timezone-aware"):
        naive_trace.to_json()

    with tracer.trace("large", input="x" * 100) as large_trace:
        pass
    with pytest.raises(TraceSerializationError, match="maximum size"):
        large_trace.to_json(max_bytes=50)

    with tracer.trace("non-finite") as non_finite_trace:
        with non_finite_trace.span(
            "llm", kind=SpanKind.LLM, llm=LLMAttributes(temperature=float("nan"))
        ):
            pass
    with pytest.raises(TraceSerializationError, match="non-finite"):
        non_finite_trace.to_dict()


def test_invalid_lifecycle_operations_fail_predictably() -> None:
    tracer = Tracer()
    trace = tracer.trace("lifecycle")

    with pytest.raises(RuntimeError, match="not active"):
        trace.set_output("too-early")
    with pytest.raises(RuntimeError, match="active trace"):
        trace.span("too-early")

    with trace:
        span = trace.span("once")
        with pytest.raises(RuntimeError, match="not active"):
            span.set_output("too-early")
        with span:
            pass
        with pytest.raises(RuntimeError, match="already finished"):
            span.set_output("late")
        with pytest.raises(RuntimeError, match="cannot be entered more than once"):
            with span:
                pass

    with pytest.raises(RuntimeError, match="already finished"):
        trace.set_output("late")
    with pytest.raises(RuntimeError, match="cannot be entered more than once"):
        with trace:
            pass


async def test_concurrent_async_traces_and_nested_spans_are_isolated() -> None:
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)
    ready = asyncio.Event()
    observed: dict[str, tuple[str, str]] = {}

    async def instrument(name: str) -> None:
        async with tracer.trace(name) as trace:
            async with trace.span("parent") as parent:
                if name == "first":
                    ready.set()
                    await asyncio.sleep(0)
                else:
                    await ready.wait()
                async with tracer.span("child") as child:
                    await asyncio.sleep(0)
                    assert tracer.current_trace is trace
                    assert tracer.current_span is child
                    observed[name] = (trace.trace_id, parent.span_id)

    await asyncio.gather(instrument("first"), instrument("second"))

    assert len({trace_id for trace_id, _ in observed.values()}) == 2
    assert len({span_id for _, span_id in observed.values()}) == 2
    assert len(exporter.traces) == 2
    for trace in exporter.traces:
        assert trace.spans[1].parent_span_id == trace.spans[0].span_id
    assert tracer.current_trace is None
    assert tracer.current_span is None


def test_token_usage_validates_counts_and_derives_total() -> None:
    usage = TokenUsage(input_tokens=3, output_tokens=4)

    assert usage.total_tokens == 7
    with pytest.raises(ValueError, match="non-negative"):
        TokenUsage(input_tokens=-1)
    with pytest.raises(ValueError, match="must equal"):
        TokenUsage(input_tokens=1, output_tokens=1, total_tokens=3)
    with pytest.raises(ValueError, match="integers"):
        TokenUsage(input_tokens=True)
