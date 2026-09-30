from __future__ import annotations

import json
import math
import time
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Literal, Protocol, Self, cast

type JsonPrimitive = None | bool | int | float | str
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]

DEFAULT_MAX_JSON_BYTES = 1_048_576
_MAX_JSON_DEPTH = 64


class TraceSerializationError(ValueError):
    """Raised when trace data cannot be represented by the JSON contract."""


class SpanKind(StrEnum):
    """The role of an operation within an agent trace."""

    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    RETRIEVAL = "retrieval"
    WORKFLOW = "workflow"
    CUSTOM = "custom"


class ExecutionStatus(StrEnum):
    """Lifecycle state for a trace or span."""

    UNSET = "unset"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TraceError:
    """Safe structured exception details without a traceback or exception object."""

    type: str
    message: str


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Provider-reported LLM token counts."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    def __post_init__(self) -> None:
        counts = (self.input_tokens, self.output_tokens, self.total_tokens)
        if any(
            value is not None and (isinstance(value, bool) or not isinstance(value, int))
            for value in counts
        ):
            raise ValueError("token counts must be integers or null")
        if any(value is not None and value < 0 for value in counts):
            raise ValueError("token counts must be non-negative")
        if self.input_tokens is not None and self.output_tokens is not None:
            calculated = self.input_tokens + self.output_tokens
            if self.total_tokens is None:
                object.__setattr__(self, "total_tokens", calculated)
            elif self.total_tokens != calculated:
                raise ValueError("total_tokens must equal input_tokens + output_tokens")


@dataclass(frozen=True, slots=True)
class ToolCall:
    """An LLM-requested tool or function call."""

    name: str
    arguments: JsonValue
    id: str | None = None
    result: JsonValue = None


@dataclass(frozen=True, slots=True)
class LLMAttributes:
    """Optional structured attributes that apply to an LLM span."""

    provider: str | None = None
    model: str | None = None
    operation: str | None = None
    token_usage: TokenUsage | None = None
    finish_reason: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    temperature: float | None = None
    attributes: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SpanEvent:
    """A timestamped event attached to a span."""

    name: str
    timestamp: datetime
    attributes: dict[str, JsonValue] = field(default_factory=dict)


class TraceExporter(Protocol):
    """Boundary for receiving one completed trace."""

    def export(self, trace: Trace) -> None:
        """Export a finalized trace."""


@dataclass(slots=True)
class Span:
    """One timed operation within a trace."""

    span_id: str
    trace_id: str
    parent_span_id: str | None
    name: str
    kind: SpanKind
    start_time: datetime
    end_time: datetime | None = None
    status: ExecutionStatus = ExecutionStatus.RUNNING
    input: JsonValue = None
    output: JsonValue = None
    error: TraceError | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    attributes: dict[str, JsonValue] = field(default_factory=dict)
    events: list[SpanEvent] = field(default_factory=list)
    llm: LLMAttributes | None = None
    duration_ms: float | None = None
    _trace: Trace | None = field(default=None, repr=False, compare=False)
    _started_monotonic: float | None = field(default=None, repr=False, compare=False)
    _context_token: Token[Span | None] | None = field(default=None, repr=False, compare=False)
    _entered: bool = field(default=False, repr=False, compare=False)

    def __enter__(self) -> Self:
        if self._entered:
            raise RuntimeError("span cannot be entered more than once")
        if self._trace is None or _active_trace.get() is not self._trace:
            raise RuntimeError("span must be entered while its trace is active")
        self._entered = True
        self._started_monotonic = time.perf_counter()
        self.start_time = _utc_now()
        self._trace.spans.append(self)
        self._context_token = _active_span.set(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> Literal[False]:
        self._finish(exc)
        if self._context_token is not None:
            _active_span.reset(self._context_token)
            self._context_token = None
        return False

    async def __aenter__(self) -> Self:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> Literal[False]:
        return self.__exit__(exc_type, exc, traceback)

    def set_output(self, output: JsonValue) -> None:
        """Record an operation result before the span finishes."""

        self._ensure_running()
        self.output = output

    def add_event(
        self, name: str, *, attributes: dict[str, JsonValue] | None = None
    ) -> SpanEvent:
        """Record a timestamped event on the active span."""

        self._ensure_running()
        event = SpanEvent(name=name, timestamp=_utc_now(), attributes=attributes or {})
        self.events.append(event)
        return event

    def _ensure_running(self) -> None:
        if self.status is not ExecutionStatus.RUNNING or self.end_time is not None:
            raise RuntimeError("span is already finished")
        if not self._entered or self._trace is None or _active_trace.get() is not self._trace:
            raise RuntimeError("span is not active")

    def _finish(self, exc: BaseException | None) -> None:
        self._ensure_running()
        self.end_time = _utc_now()
        started = self._started_monotonic
        self.duration_ms = max(0.0, (time.perf_counter() - started) * 1000) if started else 0.0
        if exc is None:
            self.status = ExecutionStatus.SUCCESS
        else:
            self.status = ExecutionStatus.ERROR
            self.error = TraceError(type=type(exc).__name__, message=str(exc))

    def _finish_from_callback(self, exc: BaseException | None, parent: Span | None) -> None:
        """Finish when a framework ends work in a copied context."""

        try:
            self._finish(exc)
        finally:
            self._context_token = None
            _active_span.set(parent)

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the stable JSON-compatible representation of this span."""

        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "kind": self.kind.value,
            "start_time": _format_timestamp(self.start_time, "span.start_time"),
            "end_time": _format_optional_timestamp(self.end_time, "span.end_time"),
            "status": self.status.value,
            "input": _normalize_json(self.input, "span.input"),
            "output": _normalize_json(self.output, "span.output"),
            "error": _error_to_dict(self.error),
            "metadata": _normalize_json(self.metadata, "span.metadata"),
            "attributes": _normalize_json(self.attributes, "span.attributes"),
            "events": [_event_to_dict(event) for event in self.events],
            "llm": _llm_to_dict(self.llm),
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Self:
        """Restore a span produced by :meth:`to_dict`."""

        error_data = _optional_mapping(data.get("error"), "span.error")
        llm_data = _optional_mapping(data.get("llm"), "span.llm")
        events_data = _list(data["events"], "span.events")
        span = cls(
            span_id=_string(data["span_id"], "span.span_id"),
            trace_id=_string(data["trace_id"], "span.trace_id"),
            parent_span_id=_optional_string(data.get("parent_span_id"), "span.parent_span_id"),
            name=_string(data["name"], "span.name"),
            kind=SpanKind(_string(data["kind"], "span.kind")),
            start_time=_parse_timestamp(data["start_time"], "span.start_time"),
            end_time=_parse_optional_timestamp(data.get("end_time"), "span.end_time"),
            status=ExecutionStatus(_string(data["status"], "span.status")),
            input=_normalize_json(data.get("input"), "span.input"),
            output=_normalize_json(data.get("output"), "span.output"),
            error=_error_from_dict(error_data),
            metadata=_json_mapping(data.get("metadata"), "span.metadata"),
            attributes=_json_mapping(data.get("attributes"), "span.attributes"),
            events=[_event_from_dict(_mapping(item, "span.events[]")) for item in events_data],
            llm=_llm_from_dict(llm_data),
            duration_ms=_optional_number(data.get("duration_ms"), "span.duration_ms"),
        )
        span._entered = span.end_time is not None
        return span


@dataclass(slots=True)
class Trace:
    """One logical agent execution and all of its spans."""

    trace_id: str
    name: str
    start_time: datetime
    end_time: datetime | None = None
    status: ExecutionStatus = ExecutionStatus.RUNNING
    input: JsonValue = None
    output: JsonValue = None
    error: TraceError | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    spans: list[Span] = field(default_factory=list)
    duration_ms: float | None = None
    _owner: Tracer | None = field(default=None, repr=False, compare=False)
    _exporter: TraceExporter | None = field(default=None, repr=False, compare=False)
    _started_monotonic: float | None = field(default=None, repr=False, compare=False)
    _context_token: Token[Trace | None] | None = field(default=None, repr=False, compare=False)
    _span_context_token: Token[Span | None] | None = field(default=None, repr=False, compare=False)
    _entered: bool = field(default=False, repr=False, compare=False)

    def __enter__(self) -> Self:
        if self._entered:
            raise RuntimeError("trace cannot be entered more than once")
        self._entered = True
        self._started_monotonic = time.perf_counter()
        self.start_time = _utc_now()
        self._context_token = _active_trace.set(self)
        self._span_context_token = _active_span.set(None)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> Literal[False]:
        self._finish(exc)
        if self._span_context_token is not None:
            _active_span.reset(self._span_context_token)
            self._span_context_token = None
        if self._context_token is not None:
            _active_trace.reset(self._context_token)
            self._context_token = None
        if self._exporter is not None:
            try:
                self._exporter.export(self)
            except Exception as export_error:
                if exc is None:
                    raise
                exc.add_note(f"AgentScope trace export failed: {export_error}")
        return False

    async def __aenter__(self) -> Self:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> Literal[False]:
        return self.__exit__(exc_type, exc, traceback)

    def span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.CUSTOM,
        input: JsonValue = None,
        metadata: dict[str, JsonValue] | None = None,
        attributes: dict[str, JsonValue] | None = None,
        llm: LLMAttributes | None = None,
    ) -> Span:
        """Create a span nested under the active span in this trace, if present."""

        if _active_trace.get() is not self or self.status is not ExecutionStatus.RUNNING:
            raise RuntimeError("spans can only be created inside their active trace")
        parent = _active_span.get()
        parent_id = (
            parent.span_id if parent is not None and parent.trace_id == self.trace_id else None
        )
        return Span(
            span_id=_new_id("sp"),
            trace_id=self.trace_id,
            parent_span_id=parent_id,
            name=name,
            kind=kind,
            start_time=_utc_now(),
            input=input,
            metadata=metadata or {},
            attributes=attributes or {},
            llm=llm,
            _trace=self,
        )

    def set_output(self, output: JsonValue) -> None:
        """Record the trace result before the trace finishes."""

        self._ensure_running()
        self.output = output

    def _ensure_running(self) -> None:
        if self.status is not ExecutionStatus.RUNNING or self.end_time is not None:
            raise RuntimeError("trace is already finished")
        if not self._entered or _active_trace.get() is not self:
            raise RuntimeError("trace is not active")

    def _finish(self, exc: BaseException | None) -> None:
        self._ensure_running()
        self.end_time = _utc_now()
        started = self._started_monotonic
        self.duration_ms = max(0.0, (time.perf_counter() - started) * 1000) if started else 0.0
        if exc is None:
            self.status = ExecutionStatus.SUCCESS
        else:
            self.status = ExecutionStatus.ERROR
            self.error = TraceError(type=type(exc).__name__, message=str(exc))

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the stable JSON-compatible representation of this trace."""

        return {
            "trace_id": self.trace_id,
            "name": self.name,
            "start_time": _format_timestamp(self.start_time, "trace.start_time"),
            "end_time": _format_optional_timestamp(self.end_time, "trace.end_time"),
            "status": self.status.value,
            "input": _normalize_json(self.input, "trace.input"),
            "output": _normalize_json(self.output, "trace.output"),
            "error": _error_to_dict(self.error),
            "metadata": _normalize_json(self.metadata, "trace.metadata"),
            "tags": list(self.tags),
            "spans": [span.to_dict() for span in self.spans],
            "duration_ms": self.duration_ms,
        }

    def to_json(self, *, indent: int | None = None, max_bytes: int = DEFAULT_MAX_JSON_BYTES) -> str:
        """Serialize the trace, rejecting unsafe values and oversized payloads."""

        try:
            encoded = json.dumps(
                self.to_dict(),
                allow_nan=False,
                ensure_ascii=False,
                indent=indent,
                separators=None if indent is not None else (",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise TraceSerializationError(str(error)) from error
        if len(encoded.encode("utf-8")) > max_bytes:
            raise TraceSerializationError(
                f"serialized trace exceeds maximum size of {max_bytes} bytes"
            )
        return encoded

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Self:
        """Restore a trace produced by :meth:`to_dict`."""

        error_data = _optional_mapping(data.get("error"), "trace.error")
        spans_data = _list(data["spans"], "trace.spans")
        tags_data = _list(data["tags"], "trace.tags")
        trace = cls(
            trace_id=_string(data["trace_id"], "trace.trace_id"),
            name=_string(data["name"], "trace.name"),
            start_time=_parse_timestamp(data["start_time"], "trace.start_time"),
            end_time=_parse_optional_timestamp(data.get("end_time"), "trace.end_time"),
            status=ExecutionStatus(_string(data["status"], "trace.status")),
            input=_normalize_json(data.get("input"), "trace.input"),
            output=_normalize_json(data.get("output"), "trace.output"),
            error=_error_from_dict(error_data),
            metadata=_json_mapping(data.get("metadata"), "trace.metadata"),
            tags=tuple(_string(tag, "trace.tags[]") for tag in tags_data),
            spans=[Span.from_dict(_mapping(item, "trace.spans[]")) for item in spans_data],
            duration_ms=_optional_number(data.get("duration_ms"), "trace.duration_ms"),
        )
        trace._entered = trace.end_time is not None
        return trace

    @classmethod
    def from_json(cls, payload: str, *, max_bytes: int = DEFAULT_MAX_JSON_BYTES) -> Self:
        """Deserialize a trace while enforcing the same payload-size boundary."""

        if len(payload.encode("utf-8")) > max_bytes:
            raise TraceSerializationError(
                f"serialized trace exceeds maximum size of {max_bytes} bytes"
            )
        try:
            raw = json.loads(payload)
            return cls.from_dict(_mapping(raw, "trace"))
        except TraceSerializationError:
            raise
        except (KeyError, RecursionError, TypeError, ValueError) as error:
            raise TraceSerializationError(f"invalid trace payload: {error}") from error


class InMemoryTraceExporter:
    """Thread-safe exporter that retains completed traces in memory."""

    def __init__(self) -> None:
        self._traces: list[Trace] = []
        self._lock = Lock()

    def export(self, trace: Trace) -> None:
        with self._lock:
            self._traces.append(trace)

    @property
    def traces(self) -> tuple[Trace, ...]:
        with self._lock:
            return tuple(self._traces)

    def clear(self) -> None:
        """Discard retained traces."""

        with self._lock:
            self._traces.clear()


class Tracer:
    """Creates traces and exposes context-local active trace/span state."""

    def __init__(self, *, exporter: TraceExporter | None = None) -> None:
        self._exporter = exporter

    def trace(
        self,
        name: str,
        *,
        input: JsonValue = None,
        metadata: dict[str, JsonValue] | None = None,
        tags: tuple[str, ...] = (),
    ) -> Trace:
        """Create a trace context with identity assigned before export."""

        return Trace(
            trace_id=_new_id("tr"),
            name=name,
            start_time=_utc_now(),
            input=input,
            metadata=metadata or {},
            tags=tags,
            _owner=self,
            _exporter=self._exporter,
        )

    def span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.CUSTOM,
        input: JsonValue = None,
        metadata: dict[str, JsonValue] | None = None,
        attributes: dict[str, JsonValue] | None = None,
        llm: LLMAttributes | None = None,
    ) -> Span:
        """Create a span in this tracer's active trace."""

        trace = self.current_trace
        if trace is None:
            raise RuntimeError("an active trace is required to create a span")
        return trace.span(
            name,
            kind=kind,
            input=input,
            metadata=metadata,
            attributes=attributes,
            llm=llm,
        )

    @property
    def current_trace(self) -> Trace | None:
        trace = _active_trace.get()
        return trace if trace is not None and trace._owner is self else None

    @property
    def current_span(self) -> Span | None:
        span = _active_span.get()
        if span is None or span._trace is None or span._trace._owner is not self:
            return None
        return span


_active_trace: ContextVar[Trace | None] = ContextVar("agentscope_active_trace", default=None)
_active_span: ContextVar[Span | None] = ContextVar("agentscope_active_span", default=None)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_timestamp(value: datetime, path: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TraceSerializationError(f"{path} must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _format_optional_timestamp(value: datetime | None, path: str) -> str | None:
    return None if value is None else _format_timestamp(value, path)


def _parse_timestamp(value: object, path: str) -> datetime:
    text = _string(value, path)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TraceSerializationError(f"{path} must be timezone-aware")
    return parsed.astimezone(UTC)


def _parse_optional_timestamp(value: object, path: str) -> datetime | None:
    return None if value is None else _parse_timestamp(value, path)


def _normalize_json(
    value: object,
    path: str,
    *,
    _seen: set[int] | None = None,
    _depth: int = 0,
) -> JsonValue:
    if _depth > _MAX_JSON_DEPTH:
        raise TraceSerializationError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TraceSerializationError(f"{path} contains a non-finite float")
        return value
    if not isinstance(value, (list, dict)):
        raise TraceSerializationError(
            f"{path} must contain only JSON-compatible values, got {type(value).__name__}"
        )

    seen = _seen if _seen is not None else set()
    identity = id(value)
    if identity in seen:
        raise TraceSerializationError(f"{path} contains a recursive structure")
    seen.add(identity)
    try:
        if isinstance(value, list):
            return [
                _normalize_json(item, f"{path}[{index}]", _seen=seen, _depth=_depth + 1)
                for index, item in enumerate(value)
            ]
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TraceSerializationError(f"{path} contains a non-string mapping key")
            normalized[key] = _normalize_json(
                item, f"{path}.{key}", _seen=seen, _depth=_depth + 1
            )
        return normalized
    finally:
        seen.remove(identity)


def _mapping(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TraceSerializationError(f"{path} must be an object with string keys")
    return cast(dict[str, object], value)


def _optional_mapping(value: object, path: str) -> dict[str, object] | None:
    return None if value is None else _mapping(value, path)


def _json_mapping(value: object, path: str) -> dict[str, JsonValue]:
    normalized = _normalize_json(value, path)
    if not isinstance(normalized, dict):
        raise TraceSerializationError(f"{path} must be an object")
    return normalized


def _list(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise TraceSerializationError(f"{path} must be an array")
    return cast(list[object], value)


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise TraceSerializationError(f"{path} must be a string")
    return value


def _optional_string(value: object, path: str) -> str | None:
    return None if value is None else _string(value, path)


def _optional_int(value: object, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TraceSerializationError(f"{path} must be an integer or null")
    return value


def _optional_number(value: object, path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TraceSerializationError(f"{path} must be a number or null")
    result = float(value)
    if not math.isfinite(result):
        raise TraceSerializationError(f"{path} must be finite")
    return result


def _error_to_dict(error: TraceError | None) -> dict[str, JsonValue] | None:
    return None if error is None else {"type": error.type, "message": error.message}


def _error_from_dict(data: dict[str, object] | None) -> TraceError | None:
    if data is None:
        return None
    return TraceError(
        type=_string(data["type"], "error.type"),
        message=_string(data["message"], "error.message"),
    )


def _event_to_dict(event: SpanEvent) -> dict[str, JsonValue]:
    return {
        "name": event.name,
        "timestamp": _format_timestamp(event.timestamp, "event.timestamp"),
        "attributes": _normalize_json(event.attributes, "event.attributes"),
    }


def _event_from_dict(data: dict[str, object]) -> SpanEvent:
    return SpanEvent(
        name=_string(data["name"], "event.name"),
        timestamp=_parse_timestamp(data["timestamp"], "event.timestamp"),
        attributes=_json_mapping(data.get("attributes"), "event.attributes"),
    )


def _tool_call_to_dict(call: ToolCall) -> dict[str, JsonValue]:
    return {
        "id": call.id,
        "name": call.name,
        "arguments": _normalize_json(call.arguments, "tool_call.arguments"),
        "result": _normalize_json(call.result, "tool_call.result"),
    }


def _tool_call_from_dict(data: dict[str, object]) -> ToolCall:
    return ToolCall(
        id=_optional_string(data.get("id"), "tool_call.id"),
        name=_string(data["name"], "tool_call.name"),
        arguments=_normalize_json(data.get("arguments"), "tool_call.arguments"),
        result=_normalize_json(data.get("result"), "tool_call.result"),
    )


def _token_usage_to_dict(usage: TokenUsage | None) -> dict[str, JsonValue] | None:
    if usage is None:
        return None
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }


def _token_usage_from_dict(data: dict[str, object] | None) -> TokenUsage | None:
    if data is None:
        return None
    return TokenUsage(
        input_tokens=_optional_int(data.get("input_tokens"), "token_usage.input_tokens"),
        output_tokens=_optional_int(data.get("output_tokens"), "token_usage.output_tokens"),
        total_tokens=_optional_int(data.get("total_tokens"), "token_usage.total_tokens"),
    )


def _llm_to_dict(llm: LLMAttributes | None) -> dict[str, JsonValue] | None:
    if llm is None:
        return None
    return {
        "provider": llm.provider,
        "model": llm.model,
        "operation": llm.operation,
        "token_usage": _token_usage_to_dict(llm.token_usage),
        "finish_reason": llm.finish_reason,
        "tool_calls": [_tool_call_to_dict(call) for call in llm.tool_calls],
        "temperature": _normalize_json(llm.temperature, "llm.temperature"),
        "attributes": _normalize_json(llm.attributes, "llm.attributes"),
    }


def _llm_from_dict(data: dict[str, object] | None) -> LLMAttributes | None:
    if data is None:
        return None
    calls = _list(data.get("tool_calls", []), "llm.tool_calls")
    return LLMAttributes(
        provider=_optional_string(data.get("provider"), "llm.provider"),
        model=_optional_string(data.get("model"), "llm.model"),
        operation=_optional_string(data.get("operation"), "llm.operation"),
        token_usage=_token_usage_from_dict(
            _optional_mapping(data.get("token_usage"), "llm.token_usage")
        ),
        finish_reason=_optional_string(data.get("finish_reason"), "llm.finish_reason"),
        tool_calls=tuple(
            _tool_call_from_dict(_mapping(call, "llm.tool_calls[]")) for call in calls
        ),
        temperature=_optional_number(data.get("temperature"), "llm.temperature"),
        attributes=_json_mapping(data.get("attributes"), "llm.attributes"),
    )
