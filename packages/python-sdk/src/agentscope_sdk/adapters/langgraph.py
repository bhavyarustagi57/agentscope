"""Explicit LangGraph instrumentation through per-invocation callbacks.

Integration contract: https://reference.langchain.com/python/langgraph/pregel/main/Pregel
Node metadata: https://docs.langchain.com/oss/python/langgraph/graph-api#nodes
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from enum import StrEnum
from threading import Lock
from typing import Any, Never, Protocol, TypeVar
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import merge_configs

from agentscope_sdk.core import JsonValue, Span, SpanKind, Tracer

__all__ = ["LangGraphCapture", "LangGraphInstrumentation"]

InputT = TypeVar("InputT", contravariant=True)
OutputT = TypeVar("OutputT", covariant=True)
_MAX_CAPTURE_DEPTH = 64


class LangGraphCapture(StrEnum):
    """Graph/node state capture policy. Metadata-only is the safe default."""

    METADATA = "metadata"
    INPUTS = "inputs"
    OUTPUTS = "outputs"
    ALL = "all"


class _Graph(Protocol[InputT, OutputT]):
    def get_name(self) -> str: ...

    def invoke(
        self,
        input: InputT,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> OutputT: ...

    async def ainvoke(
        self,
        input: InputT,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> OutputT: ...


class LangGraphInstrumentation:
    """Run one graph with AgentScope workflow/node spans and no global patching."""

    def __init__(
        self,
        tracer: Tracer,
        *,
        capture: LangGraphCapture = LangGraphCapture.METADATA,
    ) -> None:
        self._tracer = tracer
        self.capture = capture

    def invoke(
        self,
        graph: _Graph[InputT, OutputT],
        input: InputT,
        config: RunnableConfig | None = None,
        *,
        name: str | None = None,
        **kwargs: Any,
    ) -> OutputT:
        """Invoke a graph, owning a trace only when none is already active."""

        workflow_name = name or graph.get_name()
        with self._workflow(workflow_name) as workflow:
            _record_capture(workflow, "graph_input", input, self._captures_inputs())
            result = graph.invoke(
                input,
                merge_configs(config, {"callbacks": [_NodeCallback(self._tracer, self.capture)]}),
                **kwargs,
            )
            _record_capture(workflow, "graph_output", result, self._captures_outputs())
            return result

    async def ainvoke(
        self,
        graph: _Graph[InputT, OutputT],
        input: InputT,
        config: RunnableConfig | None = None,
        *,
        name: str | None = None,
        **kwargs: Any,
    ) -> OutputT:
        """Asynchronously invoke a graph with the same ownership contract as invoke."""

        workflow_name = name or graph.get_name()
        with self._workflow(workflow_name) as workflow:
            _record_capture(workflow, "graph_input", input, self._captures_inputs())
            result = await graph.ainvoke(
                input,
                merge_configs(config, {"callbacks": [_NodeCallback(self._tracer, self.capture)]}),
                **kwargs,
            )
            _record_capture(workflow, "graph_output", result, self._captures_outputs())
            return result

    def stream(self, *args: object, **kwargs: object) -> Never:
        raise NotImplementedError("LangGraph stream instrumentation is not supported")

    def astream(self, *args: object, **kwargs: object) -> Never:
        raise NotImplementedError("LangGraph astream instrumentation is not supported")

    @contextmanager
    def _workflow(self, name: str) -> Iterator[Span]:
        attributes: dict[str, JsonValue] = {"framework": "langgraph"}
        if self._tracer.current_trace is None:
            with self._tracer.trace(name) as trace:
                with trace.span(name, kind=SpanKind.WORKFLOW, attributes=attributes) as span:
                    yield span
        else:
            with self._tracer.span(name, kind=SpanKind.WORKFLOW, attributes=attributes) as span:
                yield span

    def _captures_inputs(self) -> bool:
        return self.capture in {LangGraphCapture.INPUTS, LangGraphCapture.ALL}

    def _captures_outputs(self) -> bool:
        return self.capture in {LangGraphCapture.OUTPUTS, LangGraphCapture.ALL}


class _NodeCallback(BaseCallbackHandler):
    run_inline = True

    def __init__(self, tracer: Tracer, capture: LangGraphCapture) -> None:
        self._tracer = tracer
        self._capture = capture
        self._spans: dict[UUID, tuple[Span, Span | None]] = {}
        self._lock = Lock()

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if not _is_node(tags, metadata):
            return
        assert metadata is not None
        node_name = metadata["langgraph_node"]
        assert isinstance(node_name, str)
        parent = self._tracer.current_span
        span = self._tracer.span(
            node_name,
            kind=SpanKind.CUSTOM,
            attributes=_node_attributes(metadata),
        )
        span.__enter__()
        _record_capture(
            span,
            "node_input",
            inputs,
            self._capture in {LangGraphCapture.INPUTS, LangGraphCapture.ALL},
        )
        with self._lock:
            self._spans[run_id] = (span, parent)

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        active = self._pop(run_id)
        if active is None:
            return
        span, parent = active
        _record_capture(
            span,
            "node_output",
            outputs,
            self._capture in {LangGraphCapture.OUTPUTS, LangGraphCapture.ALL},
        )
        span._finish_from_callback(None, parent)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        active = self._pop(run_id)
        if active is not None:
            span, parent = active
            span._finish_from_callback(error, parent)

    def _pop(self, run_id: UUID) -> tuple[Span, Span | None] | None:
        with self._lock:
            return self._spans.pop(run_id, None)


def _is_node(tags: Sequence[str] | None, metadata: Mapping[str, object] | None) -> bool:
    return bool(
        metadata
        and isinstance(metadata.get("langgraph_node"), str)
        and tags
        and any(tag.startswith("graph:step:") for tag in tags)
    )


def _node_attributes(metadata: Mapping[str, object]) -> dict[str, JsonValue]:
    attributes: dict[str, JsonValue] = {
        "framework": "langgraph",
        "langgraph.node": str(metadata["langgraph_node"]),
    }
    step = metadata.get("langgraph_step")
    if isinstance(step, int) and not isinstance(step, bool):
        attributes["langgraph.step"] = step
    for source, target in (
        ("langgraph_triggers", "langgraph.triggers"),
        ("langgraph_path", "langgraph.path"),
    ):
        value = metadata.get(source)
        if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
            attributes[target] = list(value)
    checkpoint_namespace = metadata.get("langgraph_checkpoint_ns")
    if isinstance(checkpoint_namespace, str):
        attributes["langgraph.checkpoint_ns"] = checkpoint_namespace
    return attributes


def _record_capture(span: Span, stage: str, value: object, enabled: bool) -> None:
    if not enabled:
        return
    try:
        normalized = _json_value(value, stage)
        if stage.endswith("input"):
            span.input = normalized
        else:
            span.set_output(normalized)
    except Exception as error:
        span.add_event(
            "instrumentation.error",
            attributes={"stage": stage, "type": type(error).__name__},
        )


def _json_value(value: object, path: str, *, depth: int = 0) -> JsonValue:
    if depth > _MAX_CAPTURE_DEPTH:
        raise ValueError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, list):
        return [_json_value(item, f"{path}[]", depth=depth + 1) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {
            key: _json_value(item, f"{path}.{key}", depth=depth + 1)
            for key, item in value.items()
        }
    raise TypeError(f"{path} is not an AgentScope JSON value")
