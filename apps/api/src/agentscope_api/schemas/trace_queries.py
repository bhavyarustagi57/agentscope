from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from agentscope_api.schemas.traces import (
    ErrorV1,
    LLMAttributesV1,
    SpanEventV1,
    SpanKind,
    TraceStatus,
)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_CURSOR_LENGTH = 1_024
_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class InvalidCursorError(ValueError):
    pass


class CursorFilterMismatchError(ValueError):
    pass


class TraceListParams(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    page_size: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE
    cursor: Annotated[str | None, Field(max_length=MAX_CURSOR_LENGTH)] = None
    status: TraceStatus | None = None
    name: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    started_after: datetime | None = None
    started_before: datetime | None = None
    min_duration_ms: Annotated[float | None, Field(ge=0)] = None
    max_duration_ms: Annotated[float | None, Field(ge=0)] = None
    has_error: bool | None = None
    span_kind: SpanKind | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        for value in (self.started_after, self.started_before):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("time filters must be timezone-aware")
        if (
            self.started_after is not None
            and self.started_before is not None
            and self.started_after > self.started_before
        ):
            raise ValueError("started_after must not exceed started_before")
        if (
            self.min_duration_ms is not None
            and self.max_duration_ms is not None
            and self.min_duration_ms > self.max_duration_ms
        ):
            raise ValueError("minimum duration must not exceed maximum duration")
        return self

    def filter_fingerprint(self) -> str:
        values = self.model_dump(
            mode="json",
            exclude={"cursor", "page_size"},
            exclude_none=True,
        )
        canonical = json.dumps(values, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class _Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    started_at: datetime
    trace_id: Annotated[str, Field(min_length=1, max_length=128)]
    filters: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        if self.version != 1:
            raise ValueError("unsupported cursor version")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("cursor timestamp must be timezone-aware")
        return self


def encode_cursor(started_at: datetime, trace_id: str, params: TraceListParams) -> str:
    payload = {
        "version": 1,
        "started_at": started_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "trace_id": trace_id,
        "filters": params.filter_fingerprint(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(value: str, params: TraceListParams) -> _Cursor:
    if len(value) > MAX_CURSOR_LENGTH or not _CURSOR_PATTERN.fullmatch(value):
        raise InvalidCursorError("invalid cursor")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(value + padding, altchars=b"-_", validate=True)
        cursor = _Cursor.model_validate_json(raw)
    except Exception as error:
        raise InvalidCursorError("invalid cursor") from error
    if cursor.filters != params.filter_fingerprint():
        raise CursorFilterMismatchError("cursor filters do not match query filters")
    return cursor


class TraceSummary(BaseModel):
    trace_id: str
    name: str
    status: TraceStatus
    started_at: datetime
    ended_at: datetime | None
    duration_ms: float | None
    ingested_at: datetime
    tags: list[str]
    output_available: bool
    span_count: int
    error_span_count: int
    llm_span_count: int
    tool_span_count: int
    total_input_tokens: int | None
    total_output_tokens: int | None
    total_tokens: int | None


class TraceListResponse(BaseModel):
    items: list[TraceSummary]
    next_cursor: str | None
    has_more: bool


class SpanDetail(BaseModel):
    span_id: str
    trace_id: str
    parent_span_id: str | None
    name: str
    kind: SpanKind
    status: TraceStatus
    started_at: datetime
    ended_at: datetime | None
    duration_ms: float | None
    input: JsonValue
    output: JsonValue
    error: ErrorV1 | None
    metadata: dict[str, JsonValue]
    attributes: dict[str, JsonValue]
    events: list[SpanEventV1]
    llm: LLMAttributesV1 | None


class TraceDetail(BaseModel):
    trace_id: str
    name: str
    status: TraceStatus
    started_at: datetime
    ended_at: datetime | None
    duration_ms: float | None
    ingested_at: datetime
    input: JsonValue
    output: JsonValue
    error: ErrorV1 | None
    metadata: dict[str, JsonValue]
    tags: list[str]
    spans: list[SpanDetail]
