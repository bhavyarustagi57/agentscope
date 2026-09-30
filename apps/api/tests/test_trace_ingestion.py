from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from typing import Any

import pytest
from agentscope_sdk import (  # noqa: E402
    LLMAttributes,
    SpanKind,
    TokenUsage,
    ToolCall,
    Trace,
    Tracer,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agentscope_api.database import SessionLocal  # noqa: E402
from agentscope_api.main import create_app  # noqa: E402
from agentscope_api.models.trace import SpanRecord, TraceRecord  # noqa: E402
from agentscope_api.schemas.traces import MAX_REQUEST_BYTES, IngestionEnvelopeV1  # noqa: E402
from agentscope_api.services.trace_ingestion import (  # noqa: E402
    DuplicateTraceConflict,
    ingest_traces,
)

pytestmark = pytest.mark.usefixtures("clean_database")


def sdk_trace_payload(name: str = "sdk-e2e") -> dict[str, Any]:
    tracer = Tracer()
    llm = LLMAttributes(
        provider="openai-compatible",
        model="example-model",
        operation="chat.completions",
        token_usage=TokenUsage(input_tokens=3, output_tokens=2, total_tokens=5),
        finish_reason="tool_calls",
        tool_calls=(
            ToolCall(
                id="call_weather",
                name="get_weather",
                arguments={"city": "Paris"},
                result={"temperature_c": 21},
            ),
        ),
        temperature=0.2,
        attributes={"provider.request_id": "request-1"},
    )
    with tracer.trace(
        name,
        input={"question": "weather"},
        metadata={"environment": "test"},
        tags=("sdk", "e2e"),
    ) as trace:
        with trace.span("agent", kind=SpanKind.AGENT, metadata={"step": 1}):
            with trace.span("model", kind=SpanKind.LLM, llm=llm) as model_span:
                model_span.add_event("first-token", attributes={"sequence": 1})
                model_span.set_output({"content": "21 C"})
            with trace.span("weather", kind=SpanKind.TOOL) as tool_span:
                tool_span.set_output({"temperature_c": 21})
        trace.set_output({"answer": "21 C"})
    return trace.to_dict()


def envelope(*traces: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": "1", "traces": list(traces)}


def golden_trace_payload() -> dict[str, Any]:
    payload = sdk_trace_payload("Golden workflow ✓")
    payload.update(
        trace_id="tr_golden_phase3",
        start_time="2026-09-17T10:00:00Z",
        end_time="2026-09-17T10:00:04Z",
        duration_ms=4_000.0,
        status="error",
        input={"question": "What is the weather in 東京?", "options": [1, True, None]},
        output={"answer": "21 °C", "sources": [{"rank": 1}]},
        error={"type": "ToolTimeout", "message": "fallback weather source timed out"},
        metadata={"environment": "integration", "nested": {"attempt": 2}},
        tags=["golden", "phase-3"],
    )
    span_ids = ["sp_workflow", "sp_llm", "sp_tool"]
    for index, span in enumerate(payload["spans"]):
        span.update(
            span_id=span_ids[index],
            trace_id=payload["trace_id"],
            parent_span_id=None if index == 0 else span_ids[index - 1],
            start_time=f"2026-09-17T10:00:0{index}Z",
            end_time=f"2026-09-17T10:00:0{index + 1}Z",
            duration_ms=1_000.0,
        )
    payload["spans"][0].update(
        name="workflow",
        kind="workflow",
        input={"goal": "research"},
        output={"plan": ["model", "tool", "verify"]},
        attributes={"workflow.version": 1},
    )
    payload["spans"][1].update(
        events=[
            {
                "name": "request.sent",
                "timestamp": "2026-09-17T10:00:01.100000Z",
                "attributes": {"sequence": 1},
            },
            {
                "name": "response.received",
                "timestamp": "2026-09-17T10:00:01.900000Z",
                "attributes": {"cached": False},
            },
        ],
        attributes={"gen_ai.request.max_tokens": 128},
        metadata={"attempt": 1},
    )
    payload["spans"][2].update(
        input={"city": "東京"},
        output={"temperature_c": 21, "conditions": ["clear"]},
        attributes={"tool.name": "get_weather"},
        metadata={"sandbox": True},
    )
    custom = deepcopy(payload["spans"][2])
    custom.update(
        span_id="sp_custom_error",
        parent_span_id="sp_tool",
        name="verify result",
        kind="custom",
        status="error",
        start_time="2026-09-17T10:00:03Z",
        end_time="2026-09-17T10:00:04Z",
        input=None,
        output=None,
        error={"type": "ToolTimeout", "message": "verification timed out"},
        metadata={},
        attributes={"retryable": True},
        events=[],
        llm=None,
    )
    payload["spans"].append(custom)
    return Trace.from_dict(payload).to_dict()


def test_sdk_payload_persists_all_fields_and_exact_retry_is_idempotent() -> None:
    payload = sdk_trace_payload()
    app = create_app()
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/traces",
            json=envelope(payload),
            headers={"Idempotency-Key": payload["trace_id"]},
        )
        retry = client.post(
            "/api/v1/traces",
            json=envelope(payload),
            headers={"Idempotency-Key": payload["trace_id"]},
        )

    assert first.status_code == 202
    assert first.json() == {"accepted": 1, "duplicates": 0}
    assert retry.status_code == 202
    assert retry.json() == {"accepted": 0, "duplicates": 1}

    async def verify() -> None:
        async with SessionLocal() as session:
            trace = await session.get(TraceRecord, payload["trace_id"])
            spans = (
                await session.scalars(
                    select(SpanRecord)
                    .where(SpanRecord.trace_id == payload["trace_id"])
                    .order_by(SpanRecord.started_at, SpanRecord.span_id)
                )
            ).all()
            assert trace is not None
            assert trace.input == {"question": "weather"}
            assert trace.output == {"answer": "21 C"}
            assert trace.metadata_ == {"environment": "test"}
            assert trace.tags == ["sdk", "e2e"]
            assert len(spans) == 3
            llm_span = next(span for span in spans if span.kind == "llm")
            assert llm_span.llm["token_usage"]["total_tokens"] == 5
            assert llm_span.events[0]["name"] == "first-token"
            assert llm_span.parent_span_id is not None

    asyncio.run(verify())


def test_golden_sdk_trace_survives_ingest_and_detail_round_trip() -> None:
    payload = golden_trace_payload()
    with TestClient(create_app()) as client:
        accepted = client.post("/api/v1/traces", json=envelope(payload))
        detail = client.get(f"/api/v1/traces/{payload['trace_id']}")

    assert accepted.status_code == 202
    assert accepted.json() == {"accepted": 1, "duplicates": 0}
    assert detail.status_code == 200
    body = detail.json()
    for field in ("trace_id", "name", "status", "input", "output", "error", "metadata", "tags"):
        assert body[field] == payload[field]
    assert datetime.fromisoformat(body["started_at"]) == datetime.fromisoformat(
        payload["start_time"].replace("Z", "+00:00")
    )
    assert datetime.fromisoformat(body["ended_at"]) == datetime.fromisoformat(
        payload["end_time"].replace("Z", "+00:00")
    )
    assert body["duration_ms"] == payload["duration_ms"]

    expected = {span["span_id"]: span for span in payload["spans"]}
    actual = {span["span_id"]: span for span in body["spans"]}
    assert set(actual) == set(expected)
    for span_id, source in expected.items():
        returned = actual[span_id]
        for field in (
            "trace_id",
            "parent_span_id",
            "name",
            "kind",
            "status",
            "input",
            "output",
            "error",
            "metadata",
            "attributes",
            "events",
            "llm",
            "duration_ms",
        ):
            assert returned[field] == source[field]
        assert datetime.fromisoformat(returned["started_at"]) == datetime.fromisoformat(
            source["start_time"].replace("Z", "+00:00")
        )
        assert datetime.fromisoformat(returned["ended_at"]) == datetime.fromisoformat(
            source["end_time"].replace("Z", "+00:00")
        )


def test_mixed_new_and_duplicate_batch() -> None:
    first = sdk_trace_payload("first")
    second = sdk_trace_payload("second")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(first)).status_code == 202
        response = client.post("/api/v1/traces", json=envelope(first, second))
    assert response.status_code == 202
    assert response.json() == {"accepted": 1, "duplicates": 1}


def test_batch_of_existing_traces_reports_each_duplicate() -> None:
    traces = [sdk_trace_payload(f"existing-{index}") for index in range(3)]
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(*traces)).json() == {
            "accepted": 3,
            "duplicates": 0,
        }
        duplicate = client.post("/api/v1/traces", json=envelope(*traces))

    assert duplicate.status_code == 202
    assert duplicate.json() == {"accepted": 0, "duplicates": 3}


def test_invalid_trace_in_batch_prevents_all_persistence() -> None:
    valid = sdk_trace_payload("valid")
    invalid = sdk_trace_payload("invalid")
    invalid["unexpected"] = True
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/traces", json=envelope(valid, invalid))

    assert response.status_code == 422

    async def verify() -> None:
        async with SessionLocal() as session:
            assert await session.scalar(select(func.count()).select_from(TraceRecord)) == 0

    asyncio.run(verify())


def test_conflicting_duplicate_returns_409_and_rolls_back_new_trace() -> None:
    original = sdk_trace_payload("original")
    conflict = deepcopy(original)
    conflict["output"] = {"answer": "materially different"}
    new_trace = sdk_trace_payload("must-roll-back")

    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(original)).status_code == 202
        response = client.post("/api/v1/traces", json=envelope(new_trace, conflict))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TRACE_CONFLICT"

    async def verify() -> None:
        async with SessionLocal() as session:
            count = await session.scalar(select(func.count()).select_from(TraceRecord))
            assert count == 1
            assert await session.get(TraceRecord, new_trace["trace_id"]) is None

    asyncio.run(verify())


async def test_concurrent_exact_duplicates_create_one_trace() -> None:
    parsed = IngestionEnvelopeV1.model_validate(envelope(sdk_trace_payload("concurrent")))

    async def submit() -> tuple[int, int]:
        async with SessionLocal() as session:
            result = await ingest_traces(session, parsed)
            return result.accepted, result.duplicates

    outcomes = await asyncio.gather(*(submit() for _ in range(12)))
    assert outcomes.count((1, 0)) == 1
    assert outcomes.count((0, 1)) == 11
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(TraceRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(SpanRecord)) == 3


async def test_concurrent_conflicting_payloads_have_one_winner_without_mixed_spans() -> None:
    first_payload = sdk_trace_payload("concurrent-first")
    second_payload = deepcopy(first_payload)
    second_payload["name"] = "concurrent-second"
    first = IngestionEnvelopeV1.model_validate(envelope(first_payload))
    second = IngestionEnvelopeV1.model_validate(envelope(second_payload))

    async def submit(value: IngestionEnvelopeV1) -> str:
        async with SessionLocal() as session:
            try:
                result = await ingest_traces(session, value)
                return f"accepted:{result.accepted}"
            except DuplicateTraceConflict:
                return "conflict"

    outcomes = await asyncio.gather(submit(first), submit(second))

    assert sorted(outcomes) == ["accepted:1", "conflict"]
    async with SessionLocal() as session:
        stored = await session.get(TraceRecord, first_payload["trace_id"])
        spans = await session.scalar(
            select(func.count())
            .select_from(SpanRecord)
            .where(SpanRecord.trace_id == first_payload["trace_id"])
        )
    assert stored is not None
    assert stored.name in {"concurrent-first", "concurrent-second"}
    assert spans == 3


async def test_database_uniqueness_rejects_duplicate_span_identity() -> None:
    first = IngestionEnvelopeV1.model_validate(envelope(sdk_trace_payload("first")))
    second_payload = sdk_trace_payload("second")
    second_payload["spans"][-1]["span_id"] = first.traces[0].spans[-1].span_id
    second = IngestionEnvelopeV1.model_validate(envelope(second_payload))

    async with SessionLocal() as session:
        await ingest_traces(session, first)
    async with SessionLocal() as session:
        with pytest.raises(IntegrityError):
            await ingest_traces(session, second)


def test_validation_version_header_and_body_limit_errors_are_safe() -> None:
    payload = sdk_trace_payload()
    with TestClient(create_app()) as client:
        unknown = client.post("/api/v1/traces", json={"schema_version": "2", "traces": [payload]})
        bad_header = client.post(
            "/api/v1/traces",
            json=envelope(payload),
            headers={"Idempotency-Key": "contains spaces"},
        )
        too_large = client.post(
            "/api/v1/traces",
            content=b"{}",
            headers={"Content-Length": str(MAX_REQUEST_BYTES + 1)},
        )
        invalid_json = client.post(
            "/api/v1/traces",
            content=b'{"schema_version":',
            headers={"Content-Type": "application/json"},
        )

    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "UNSUPPORTED_SCHEMA_VERSION"
    assert bad_header.status_code == 422
    assert bad_header.json()["error"]["code"] == "VALIDATION_ERROR"
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    assert invalid_json.status_code == 422
    assert invalid_json.json()["error"]["code"] == "VALIDATION_ERROR"


def test_persistence_error_does_not_expose_database_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentscope_api.api.routes import traces as trace_routes

    async def fail(*_: object) -> None:
        raise SQLAlchemyError("password=secret host=/private/database.sock SELECT * FROM traces")

    monkeypatch.setattr(trace_routes, "ingest_traces", fail)
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/traces", json=envelope(sdk_trace_payload()))

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "PERSISTENCE_ERROR", "message": "trace persistence failed"}
    }
    assert "secret" not in response.text
    assert "SELECT" not in response.text


async def test_parent_constraint_allows_child_before_parent_in_one_transaction() -> None:
    payload = sdk_trace_payload("out-of-order")
    payload["spans"] = [payload["spans"][1], payload["spans"][0], payload["spans"][2]]
    parsed = IngestionEnvelopeV1.model_validate(envelope(payload))
    trace = parsed.traces[0]
    assert trace.spans[0].parent_span_id is not None
    async with SessionLocal() as session:
        result = await ingest_traces(session, parsed)
    assert result.accepted == 1


async def test_database_same_trace_parent_constraint_is_enforced() -> None:
    first = IngestionEnvelopeV1.model_validate(envelope(sdk_trace_payload("one")))
    second = IngestionEnvelopeV1.model_validate(envelope(sdk_trace_payload("two")))
    async with SessionLocal() as session:
        await ingest_traces(session, first)
        await ingest_traces(session, second)

    async with SessionLocal() as session:
        span = second.traces[0].spans[0]
        with pytest.raises(IntegrityError):
            async with session.begin():
                await session.execute(
                    insert(SpanRecord.__table__).values(
                        span_id="sp_invalid_parent",
                        trace_id=second.traces[0].trace_id,
                        parent_span_id=first.traces[0].spans[0].span_id,
                        name="invalid",
                        kind="custom",
                        status="success",
                        started_at=span.start_time,
                        ended_at=span.end_time,
                        duration_ms=span.duration_ms,
                        input=None,
                        output=None,
                        error=None,
                        metadata={},
                        attributes={},
                        events=[],
                        llm=None,
                    )
                )
