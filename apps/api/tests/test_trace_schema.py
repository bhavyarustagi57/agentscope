from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any

import pytest
from pydantic import ValidationError

from agentscope_api.schemas.traces import IngestionEnvelopeV1, trace_fingerprint


def trace_payload() -> dict[str, Any]:
    start = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
    end = start + timedelta(seconds=1)
    return {
        "trace_id": "tr_1",
        "name": "agent",
        "start_time": start.isoformat().replace("+00:00", "Z"),
        "end_time": end.isoformat().replace("+00:00", "Z"),
        "status": "success",
        "input": {"question": "hello"},
        "output": {"answer": "world"},
        "error": None,
        "metadata": {"environment": "test"},
        "tags": ["sdk"],
        "spans": [
            {
                "span_id": "sp_child",
                "trace_id": "tr_1",
                "parent_span_id": "sp_parent",
                "name": "model",
                "kind": "llm",
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "status": "success",
                "input": None,
                "output": {"text": "world"},
                "error": None,
                "metadata": {},
                "attributes": {},
                "events": [],
                "llm": {
                    "provider": "openai-compatible",
                    "model": "example-model",
                    "operation": "chat.completions",
                    "token_usage": {
                        "input_tokens": 2,
                        "output_tokens": 3,
                        "total_tokens": 5,
                    },
                    "finish_reason": "stop",
                    "tool_calls": [],
                    "temperature": 0.2,
                    "attributes": {"request_id": "request-1"},
                },
                "duration_ms": 2.5,
            },
            {
                "span_id": "sp_parent",
                "trace_id": "tr_1",
                "parent_span_id": None,
                "name": "plan",
                "kind": "agent",
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "status": "success",
                "input": None,
                "output": None,
                "error": None,
                "metadata": {},
                "attributes": {},
                "events": [{"name": "planned", "timestamp": start.isoformat(), "attributes": {}}],
                "llm": None,
                "duration_ms": 5.0,
            },
            {
                "span_id": "sp_root_two",
                "trace_id": "tr_1",
                "parent_span_id": None,
                "name": "second-root",
                "kind": "custom",
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "status": "success",
                "input": None,
                "output": None,
                "error": None,
                "metadata": {},
                "attributes": {},
                "events": [],
                "llm": None,
                "duration_ms": 1.0,
            },
        ],
        "duration_ms": 10.0,
    }


def envelope(*payloads: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": "1", "traces": list(payloads) or [trace_payload()]}


def test_accepts_schema_one_parent_child_and_multiple_roots() -> None:
    parsed = IngestionEnvelopeV1.model_validate(envelope())
    assert parsed.schema_version == "1"
    assert len(parsed.traces[0].spans) == 3


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda body: body.update(schema_version="2"), "unsupported schema_version"),
        (
            lambda body: body["traces"][0]["spans"][0].update(parent_span_id="missing"),
            "missing parent",
        ),
        (
            lambda body: body["traces"][0]["spans"][0].update(parent_span_id="sp_child"),
            "own parent",
        ),
        (lambda body: body["traces"][0]["spans"][1].update(parent_span_id="sp_child"), "cycle"),
        (lambda body: body["traces"][0]["spans"][0].update(trace_id="tr_other"), "does not match"),
        (
            lambda body: body["traces"][0]["spans"][1].update(span_id="sp_child"),
            "duplicate span_id",
        ),
        (lambda body: body["traces"][0]["spans"][0].update(kind="unknown"), "kind"),
        (lambda body: body["traces"][0].update(start_time="2026-09-17T10:00:00"), "timezone-aware"),
        (lambda body: body["traces"][0].update(duration_ms=-1), "duration_ms"),
    ],
)
def test_rejects_invalid_contract(mutate: Any, message: str) -> None:
    body = envelope()
    mutate(body)
    with pytest.raises(ValidationError, match=message):
        IngestionEnvelopeV1.model_validate(body)


def test_rejects_oversized_batch() -> None:
    body = envelope()
    body["traces"] = [
        dict(trace_payload(), trace_id=f"tr_{index}", spans=[]) for index in range(11)
    ]
    with pytest.raises(ValidationError, match="at most 10"):
        IngestionEnvelopeV1.model_validate(body)


def test_rejects_duplicate_trace_ids_and_unknown_fields() -> None:
    duplicate = envelope(trace_payload(), trace_payload())
    with pytest.raises(ValidationError, match="duplicate trace_id"):
        IngestionEnvelopeV1.model_validate(duplicate)

    body = envelope()
    body["traces"][0]["unexpected"] = "field"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        IngestionEnvelopeV1.model_validate(body)


def test_rejects_error_details_on_success() -> None:
    body = envelope()
    body["traces"][0]["error"] = {"type": "RuntimeError", "message": "failed"}
    with pytest.raises(ValidationError, match="only an error trace"):
        IngestionEnvelopeV1.model_validate(body)


def test_fingerprint_is_stable_for_key_and_timestamp_spelling() -> None:
    original = trace_payload()
    timestamp_variant = deepcopy(original)
    timestamp_variant["start_time"] = timestamp_variant["start_time"].replace("Z", "+00:00")
    timestamp_variant["end_time"] = timestamp_variant["end_time"].replace("Z", "+00:00")
    for span in timestamp_variant["spans"]:
        span["start_time"] = span["start_time"].replace("+00:00", "Z")
        span["end_time"] = span["end_time"].replace("+00:00", "Z")
        for event in span["events"]:
            event["timestamp"] = event["timestamp"].replace("+00:00", "Z")

    first = IngestionEnvelopeV1.model_validate(envelope(original)).traces[0]
    same_time = IngestionEnvelopeV1.model_validate(envelope(timestamp_variant)).traces[0]
    reordered = deepcopy(trace_payload())
    reordered["metadata"] = {"z": 1, "a": 2}
    first_with_metadata = IngestionEnvelopeV1.model_validate(envelope(reordered)).traces[0]
    reordered_again = deepcopy(reordered)
    reordered_again["metadata"] = {"a": 2, "z": 1}
    second_with_metadata = IngestionEnvelopeV1.model_validate(envelope(reordered_again)).traces[0]

    assert trace_fingerprint(first) == trace_fingerprint(same_time)
    assert trace_fingerprint(first_with_metadata) == trace_fingerprint(second_with_metadata)


def test_fingerprint_changes_for_material_payload_change() -> None:
    first = IngestionEnvelopeV1.model_validate(envelope()).traces[0]
    changed = deepcopy(trace_payload())
    changed["output"] = {"answer": "different"}
    second = IngestionEnvelopeV1.model_validate(envelope(changed)).traces[0]
    assert trace_fingerprint(first) != trace_fingerprint(second)


def test_deep_hierarchy_validation_is_bounded() -> None:
    body = envelope()
    trace = body["traces"][0]
    template = trace["spans"][0]
    trace["spans"] = []
    for index in range(10_000):
        span = deepcopy(template)
        span.update(
            span_id=f"sp_{index}",
            trace_id=trace["trace_id"],
            parent_span_id=f"sp_{index - 1}" if index else None,
        )
        trace["spans"].append(span)

    started = perf_counter()
    parsed = IngestionEnvelopeV1.model_validate(body)

    assert len(parsed.traces[0].spans) == 10_000
    assert perf_counter() - started < 5
