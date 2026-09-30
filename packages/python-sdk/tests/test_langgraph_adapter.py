import asyncio
import operator
from types import SimpleNamespace
from typing import Annotated, TypedDict

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph

from agentscope_sdk import ExecutionStatus, InMemoryTraceExporter, SpanKind, Tracer
from agentscope_sdk.adapters.langgraph import LangGraphCapture, LangGraphInstrumentation
from agentscope_sdk.adapters.openai import OpenAIInstrumentation


class ValueState(TypedDict):
    value: int


def value_graph(*, fail: BaseException | None = None):
    def planner(state: ValueState) -> ValueState:
        return {"value": state["value"] + 1}

    def answer(state: ValueState) -> ValueState:
        if fail is not None:
            raise fail
        return {"value": state["value"] + 1}

    builder = StateGraph(ValueState)
    builder.add_node("planner", planner)
    builder.add_node("answer", answer)
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "answer")
    builder.add_edge("answer", END)
    return builder.compile()


def test_invoke_owns_one_trace_and_records_workflow_and_nodes() -> None:
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)
    instrumentation = LangGraphInstrumentation(tracer)

    result = instrumentation.invoke(value_graph(), {"value": 0}, name="value-graph")

    assert result == {"value": 2}
    assert len(exporter.traces) == 1
    trace = exporter.traces[0]
    assert trace.name == "value-graph"
    assert [span.name for span in trace.spans] == ["value-graph", "planner", "answer"]
    assert [span.kind for span in trace.spans] == [
        SpanKind.WORKFLOW,
        SpanKind.CUSTOM,
        SpanKind.CUSTOM,
    ]
    assert all(span.parent_span_id == trace.spans[0].span_id for span in trace.spans[1:])
    assert all(span.status is ExecutionStatus.SUCCESS for span in trace.spans)
    assert trace.input is None and trace.output is None
    assert all(span.input is None and span.output is None for span in trace.spans)
    assert tracer.current_trace is None and tracer.current_span is None


def test_invoke_inside_active_trace_adds_no_duplicate_trace() -> None:
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)
    instrumentation = LangGraphInstrumentation(tracer)

    with tracer.trace("outer") as outer:
        with outer.span("agent", kind=SpanKind.AGENT) as agent:
            result = instrumentation.invoke(value_graph(), {"value": 3}, name="nested-graph")
            assert tracer.current_span is agent

    assert result == {"value": 5}
    assert exporter.traces == (outer,)
    workflow = outer.spans[1]
    assert workflow.name == "nested-graph"
    assert workflow.parent_span_id == agent.span_id
    assert all(span.parent_span_id == workflow.span_id for span in outer.spans[2:])


def test_existing_callbacks_are_preserved_and_nested_runnables_are_not_nodes() -> None:
    class ExistingHandler(BaseCallbackHandler):
        def __init__(self) -> None:
            self.started: list[str] = []

        def on_chain_start(
            self,
            serialized: dict[str, object],
            inputs: dict[str, object],
            *,
            run_id: object,
            parent_run_id: object | None = None,
            tags: list[str] | None = None,
            metadata: dict[str, object] | None = None,
            **kwargs: object,
        ) -> None:
            self.started.append(str(kwargs.get("name")))

    def nested(state: ValueState) -> ValueState:
        return RunnableLambda(lambda value: {"value": value["value"] + 1}).invoke(state)

    builder = StateGraph(ValueState)
    builder.add_node("nested", nested)
    builder.add_edge(START, "nested")
    builder.add_edge("nested", END)
    handler = ExistingHandler()
    exporter = InMemoryTraceExporter()
    instrumentation = LangGraphInstrumentation(Tracer(exporter=exporter))

    instrumentation.invoke(
        builder.compile(),
        {"value": 0},
        config={"callbacks": [handler]},
        name="callback-merge",
    )

    assert {"LangGraph", "nested", "RunnableLambda"}.issubset(handler.started)
    assert [span.name for span in exporter.traces[0].spans] == ["callback-merge", "nested"]


def test_capture_all_records_only_json_values_without_using_repr() -> None:
    class ExplodingRepr:
        def __repr__(self) -> str:
            raise AssertionError("arbitrary graph values must not be represented")

    class ObjectState(TypedDict):
        value: object

    def preserve(state: ObjectState) -> ObjectState:
        return state

    builder = StateGraph(ObjectState)
    builder.add_node("preserve", preserve)
    builder.add_edge(START, "preserve")
    builder.add_edge("preserve", END)
    graph = builder.compile()
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)
    instrumentation = LangGraphInstrumentation(tracer, capture=LangGraphCapture.ALL)

    value = ExplodingRepr()
    result = instrumentation.invoke(graph, {"value": value}, name="safe-capture")

    assert result["value"] is value
    trace = exporter.traces[0]
    assert all(span.input is None and span.output is None for span in trace.spans)
    assert all(
        event.name == "instrumentation.error"
        for span in trace.spans
        for event in span.events
    )
    assert "ExplodingRepr" not in trace.to_json()


def test_capture_all_records_graph_and_node_inputs_and_outputs() -> None:
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)
    instrumentation = LangGraphInstrumentation(tracer, capture=LangGraphCapture.ALL)

    instrumentation.invoke(value_graph(), {"value": 0}, name="captured")

    workflow, planner, answer = exporter.traces[0].spans
    assert workflow.input == {"value": 0}
    assert workflow.output == {"value": 2}
    assert planner.input == {"value": 0}
    assert planner.output == {"value": 1}
    assert answer.input == {"value": 1}
    assert answer.output == {"value": 2}
    assert planner.attributes["langgraph.step"] == 1


def test_graph_error_marks_trace_workflow_and_node_and_restores_context() -> None:
    error = RuntimeError("graph failed")
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)
    instrumentation = LangGraphInstrumentation(tracer)

    with pytest.raises(RuntimeError) as raised:
        instrumentation.invoke(value_graph(fail=error), {"value": 0}, name="failure")

    assert raised.value is error
    trace = exporter.traces[0]
    assert trace.status is ExecutionStatus.ERROR
    assert trace.spans[0].status is ExecutionStatus.ERROR
    failed_node = next(span for span in trace.spans if span.name == "answer")
    assert failed_node.status is ExecutionStatus.ERROR
    assert failed_node.error is not None and failed_node.error.type == "RuntimeError"
    assert tracer.current_trace is None and tracer.current_span is None


class BranchState(TypedDict):
    values: Annotated[list[str], operator.add]


def async_parallel_graph():
    async def left(state: BranchState) -> BranchState:
        await asyncio.sleep(0)
        return {"values": ["left"]}

    async def right(state: BranchState) -> BranchState:
        await asyncio.sleep(0)
        return {"values": ["right"]}

    builder = StateGraph(BranchState)
    builder.add_node("left", left)
    builder.add_node("right", right)
    builder.add_edge(START, "left")
    builder.add_edge(START, "right")
    builder.add_edge("left", END)
    builder.add_edge("right", END)
    return builder.compile()


async def test_ainvoke_parallel_branches_and_concurrent_runs_are_isolated() -> None:
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)
    instrumentation = LangGraphInstrumentation(tracer)

    first, second = await asyncio.gather(
        instrumentation.ainvoke(async_parallel_graph(), {"values": []}, name="first"),
        instrumentation.ainvoke(async_parallel_graph(), {"values": []}, name="second"),
    )

    assert set(first["values"]) == {"left", "right"}
    assert set(second["values"]) == {"left", "right"}
    assert {trace.name for trace in exporter.traces} == {"first", "second"}
    for trace in exporter.traces:
        workflow = trace.spans[0]
        assert {span.name for span in trace.spans[1:]} == {"left", "right"}
        assert all(span.trace_id == trace.trace_id for span in trace.spans)
        assert all(span.parent_span_id == workflow.span_id for span in trace.spans[1:])
    assert tracer.current_trace is None and tracer.current_span is None


async def test_many_concurrent_ainvokes_do_not_cross_context_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)
    instrumentation = LangGraphInstrumentation(tracer)
    graph = async_parallel_graph()

    runs = (
        instrumentation.ainvoke(graph, {"values": []}, name=f"run-{index}")
        for index in range(50)
    )
    results = await asyncio.gather(*runs)

    assert len(results) == 50
    assert len(exporter.traces) == 50
    assert all(len(trace.spans) == 3 for trace in exporter.traces)
    assert not any("different Context" in record.getMessage() for record in caplog.records)
    assert tracer.current_trace is None and tracer.current_span is None


def test_invoke_parallel_branches_in_threads_are_isolated() -> None:
    def left(state: BranchState) -> BranchState:
        return {"values": ["left"]}

    def right(state: BranchState) -> BranchState:
        return {"values": ["right"]}

    builder = StateGraph(BranchState)
    builder.add_node("left", left)
    builder.add_node("right", right)
    builder.add_edge(START, "left")
    builder.add_edge(START, "right")
    builder.add_edge("left", END)
    builder.add_edge("right", END)
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)

    result = LangGraphInstrumentation(tracer).invoke(
        builder.compile(), {"values": []}, name="threaded"
    )

    trace = exporter.traces[0]
    assert set(result["values"]) == {"left", "right"}
    assert {span.name for span in trace.spans[1:]} == {"left", "right"}
    assert all(span.parent_span_id == trace.spans[0].span_id for span in trace.spans[1:])
    assert tracer.current_trace is None and tracer.current_span is None


async def test_async_node_error_preserves_exception_and_context() -> None:
    error = RuntimeError("async node failed")

    async def fail(state: ValueState) -> ValueState:
        await asyncio.sleep(0)
        raise error

    builder = StateGraph(ValueState)
    builder.add_node("fail", fail)
    builder.add_edge(START, "fail")
    builder.add_edge("fail", END)
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)

    with pytest.raises(RuntimeError) as raised:
        await LangGraphInstrumentation(tracer).ainvoke(
            builder.compile(), {"value": 0}, name="async-failure"
        )

    assert raised.value is error
    assert [span.status for span in exporter.traces[0].spans] == [
        ExecutionStatus.ERROR,
        ExecutionStatus.ERROR,
    ]
    assert tracer.current_trace is None and tracer.current_span is None


def test_subgraph_nodes_nest_under_the_subgraph_node() -> None:
    child_builder = StateGraph(ValueState)
    child_builder.add_node("child", lambda state: {"value": state["value"] + 1})
    child_builder.add_edge(START, "child")
    child_builder.add_edge("child", END)
    parent_builder = StateGraph(ValueState)
    parent_builder.add_node("subgraph", child_builder.compile())
    parent_builder.add_edge(START, "subgraph")
    parent_builder.add_edge("subgraph", END)
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)

    LangGraphInstrumentation(tracer).invoke(
        parent_builder.compile(), {"value": 0}, name="parent"
    )

    workflow, subgraph, child = exporter.traces[0].spans
    assert subgraph.parent_span_id == workflow.span_id
    assert child.parent_span_id == subgraph.span_id


class FakeCompletions:
    def __init__(self) -> None:
        self._count = 0

    def create(self, **kwargs: object) -> SimpleNamespace:
        self._count += 1
        return SimpleNamespace(
            id=f"completion_{self._count}",
            model="offline",
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(role="assistant", content="done", tool_calls=None),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


def test_langgraph_openai_and_tool_spans_compose_without_duplicates() -> None:
    class AgentState(TypedDict):
        value: int

    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)
    graph_instrumentation = LangGraphInstrumentation(tracer)
    openai_instrumentation = OpenAIInstrumentation(tracer)
    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    def planner(state: AgentState) -> AgentState:
        openai_instrumentation.chat_completions(client, model="offline", messages=[])
        return {"value": state["value"] + 1}

    def work(state: AgentState) -> AgentState:
        with tracer.span("offline-tool", kind=SpanKind.TOOL):
            return {"value": state["value"] + 1}

    builder = StateGraph(AgentState)
    builder.add_node("planner", planner)
    builder.add_node("work", work)
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "work")
    builder.add_edge("work", END)

    graph_instrumentation.invoke(builder.compile(), {"value": 0}, name="composed")

    trace = exporter.traces[0]
    assert len(exporter.traces) == 1
    assert [span.kind for span in trace.spans] == [
        SpanKind.WORKFLOW,
        SpanKind.CUSTOM,
        SpanKind.LLM,
        SpanKind.CUSTOM,
        SpanKind.TOOL,
    ]
    planner_node, llm, work_node, tool = trace.spans[1:]
    assert llm.parent_span_id == planner_node.span_id
    assert tool.parent_span_id == work_node.span_id
    assert sum(span.kind is SpanKind.LLM for span in trace.spans) == 1
    assert trace.to_json()


def test_streaming_modes_fail_explicitly_before_running_graph() -> None:
    instrumentation = LangGraphInstrumentation(Tracer())

    with pytest.raises(NotImplementedError, match="stream"):
        instrumentation.stream(value_graph(), {"value": 0})
    with pytest.raises(NotImplementedError, match="stream"):
        instrumentation.astream(value_graph(), {"value": 0})
