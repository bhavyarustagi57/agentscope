from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from agentscope_api.core.json_limits import validate_json_complexity

MAX_REQUEST_BYTES = 4 * 1_048_576
MAX_TRACES_PER_BATCH = 10
MAX_SPANS_PER_TRACE = 10_000
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100_000

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\s]+$")]
Name = Annotated[str, Field(min_length=1, max_length=500)]


class TraceStatus(StrEnum):
    UNSET = "unset"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


class SpanKind(StrEnum):
    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    RETRIEVAL = "retrieval"
    WORKFLOW = "workflow"
    CUSTOM = "custom"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ErrorV1(ContractModel):
    type: Annotated[str, Field(min_length=1, max_length=200)]
    message: Annotated[str, Field(max_length=4_000)]


class TokenUsageV1(ContractModel):
    input_tokens: Annotated[int, Field(ge=0)] | None
    output_tokens: Annotated[int, Field(ge=0)] | None
    total_tokens: Annotated[int, Field(ge=0)] | None

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if self.input_tokens is not None and self.output_tokens is not None:
            expected = self.input_tokens + self.output_tokens
            if self.total_tokens != expected:
                raise ValueError("total_tokens must equal input_tokens + output_tokens")
        return self


class ToolCallV1(ContractModel):
    id: Annotated[str, Field(max_length=256)] | None
    name: Name
    arguments: JsonValue
    result: JsonValue


class LLMAttributesV1(ContractModel):
    provider: Annotated[str, Field(max_length=200)] | None
    model: Annotated[str, Field(max_length=500)] | None
    operation: Annotated[str, Field(max_length=200)] | None
    token_usage: TokenUsageV1 | None
    finish_reason: Annotated[str, Field(max_length=200)] | None
    tool_calls: Annotated[list[ToolCallV1], Field(max_length=1_000)]
    temperature: float | None
    attributes: dict[str, JsonValue]


class SpanEventV1(ContractModel):
    name: Name
    timestamp: datetime
    attributes: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_timestamp(self) -> Self:
        _require_aware(self.timestamp, "event timestamp")
        return self


class SpanV1(ContractModel):
    span_id: Identifier
    trace_id: Identifier
    parent_span_id: Identifier | None
    name: Name
    kind: SpanKind
    start_time: datetime
    end_time: datetime | None
    status: TraceStatus
    input: JsonValue
    output: JsonValue
    error: ErrorV1 | None
    metadata: dict[str, JsonValue]
    attributes: dict[str, JsonValue]
    events: Annotated[list[SpanEventV1], Field(max_length=10_000)]
    llm: LLMAttributesV1 | None
    duration_ms: Annotated[float, Field(ge=0)] | None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        _validate_lifecycle(self.start_time, self.end_time, self.duration_ms)
        if self.parent_span_id == self.span_id:
            raise ValueError("span cannot be its own parent")
        if self.error is not None and self.status is not TraceStatus.ERROR:
            raise ValueError("only an error span may contain error details")
        for value in (self.input, self.output, self.metadata, self.attributes):
            validate_json_complexity(value, max_depth=MAX_JSON_DEPTH, max_nodes=MAX_JSON_NODES)
        if self.llm is not None:
            validate_json_complexity(
                self.llm.model_dump(mode="json"),
                max_depth=MAX_JSON_DEPTH,
                max_nodes=MAX_JSON_NODES,
            )
        for event in self.events:
            validate_json_complexity(
                event.attributes, max_depth=MAX_JSON_DEPTH, max_nodes=MAX_JSON_NODES
            )
        return self


class TraceV1(ContractModel):
    trace_id: Identifier
    name: Name
    start_time: datetime
    end_time: datetime | None
    status: TraceStatus
    input: JsonValue
    output: JsonValue
    error: ErrorV1 | None
    metadata: dict[str, JsonValue]
    tags: Annotated[list[Annotated[str, Field(max_length=200)]], Field(max_length=100)]
    spans: Annotated[list[SpanV1], Field(max_length=MAX_SPANS_PER_TRACE)]
    duration_ms: Annotated[float, Field(ge=0)] | None

    @model_validator(mode="after")
    def validate_trace(self) -> Self:
        _validate_lifecycle(self.start_time, self.end_time, self.duration_ms)
        if self.error is not None and self.status is not TraceStatus.ERROR:
            raise ValueError("only an error trace may contain error details")
        for value in (self.input, self.output, self.metadata):
            validate_json_complexity(value, max_depth=MAX_JSON_DEPTH, max_nodes=MAX_JSON_NODES)
        _validate_span_graph(self)
        return self


class IngestionEnvelopeV1(ContractModel):
    schema_version: str
    traces: Annotated[list[TraceV1], Field(min_length=1, max_length=MAX_TRACES_PER_BATCH)]

    @field_validator("schema_version")
    @classmethod
    def support_version_one(cls, value: str) -> str:
        if value != "1":
            raise ValueError(f"unsupported schema_version: {value}")
        return value

    @model_validator(mode="after")
    def validate_envelope(self) -> Self:
        trace_ids: set[str] = set()
        span_ids: set[str] = set()
        for trace in self.traces:
            if trace.trace_id in trace_ids:
                raise ValueError(f"duplicate trace_id in batch: {trace.trace_id}")
            trace_ids.add(trace.trace_id)
            for span in trace.spans:
                if span.span_id in span_ids:
                    raise ValueError(f"duplicate span_id in batch: {span.span_id}")
                span_ids.add(span.span_id)
        return self


class IngestionResponse(BaseModel):
    accepted: int
    duplicates: int


def trace_fingerprint(trace: TraceV1) -> str:
    canonical = json.dumps(
        trace.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _validate_lifecycle(start: datetime, end: datetime | None, duration: float | None) -> None:
    _require_aware(start, "start_time")
    if end is None:
        if duration is not None:
            raise ValueError("duration_ms requires end_time")
        return
    _require_aware(end, "end_time")
    if end < start:
        raise ValueError("end_time must not precede start_time")


def _validate_span_graph(trace: TraceV1) -> None:
    spans = {span.span_id: span for span in trace.spans}
    if len(spans) != len(trace.spans):
        raise ValueError("duplicate span_id in trace")
    for span in trace.spans:
        if span.trace_id != trace.trace_id:
            raise ValueError(f"span {span.span_id} trace_id does not match its trace")
        parent_id = span.parent_span_id
        if parent_id is not None and parent_id not in spans:
            raise ValueError(f"span {span.span_id} references missing parent {parent_id}")
        if span.start_time < trace.start_time - timedelta(seconds=1):
            raise ValueError(f"span {span.span_id} starts before its trace")
        if trace.end_time is not None and span.end_time is not None:
            if span.end_time > trace.end_time + timedelta(seconds=1):
                raise ValueError(f"span {span.span_id} ends after its trace")

    validated: set[str] = set()
    for span_id in spans:
        path: list[str] = []
        positions: set[str] = set()
        current_id = span_id
        while current_id not in validated:
            if current_id in positions:
                raise ValueError("span hierarchy contains a cycle")
            positions.add(current_id)
            path.append(current_id)
            parent_id = spans[current_id].parent_span_id
            if parent_id is None:
                break
            current_id = parent_id
        validated.update(path)
