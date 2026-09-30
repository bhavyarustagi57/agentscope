from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from test_trace_ingestion import envelope, sdk_trace_payload

from agentscope_api.database import engine
from agentscope_api.main import create_app
from agentscope_api.schemas.trace_queries import TraceListParams, decode_cursor, encode_cursor

pytestmark = pytest.mark.usefixtures("clean_database")


def retarget_trace(
    payload: dict[str, Any],
    trace_id: str,
    *,
    started_at: datetime,
    name: str | None = None,
) -> dict[str, Any]:
    payload["trace_id"] = trace_id
    payload["name"] = name or trace_id
    payload["start_time"] = started_at.isoformat()
    payload["end_time"] = (started_at + timedelta(seconds=1)).isoformat()
    span_ids = {
        span["span_id"]: f"sp_{trace_id}_{index}" for index, span in enumerate(payload["spans"])
    }
    for span in payload["spans"]:
        span["trace_id"] = trace_id
        span["span_id"] = span_ids[span["span_id"]]
        span["parent_span_id"] = span_ids.get(span["parent_span_id"])
        span["start_time"] = payload["start_time"]
        span["end_time"] = payload["end_time"]
    return payload


def test_query_parameters_and_cursor_are_strict_and_bounded() -> None:
    assert TraceListParams().page_size == 50
    assert TraceListParams(page_size=100).page_size == 100
    with pytest.raises(ValidationError):
        TraceListParams(page_size=101)
    with pytest.raises(ValidationError, match="duration"):
        TraceListParams(min_duration_ms=2, max_duration_ms=1)
    with pytest.raises(ValidationError, match="time"):
        TraceListParams(
            started_after=datetime(2026, 1, 2, tzinfo=UTC),
            started_before=datetime(2026, 1, 1, tzinfo=UTC),
        )

    params = TraceListParams(status="success", name="agent")
    cursor = encode_cursor(datetime(2026, 1, 1, tzinfo=UTC), "tr_1", params)
    decoded = decode_cursor(cursor, params)
    assert decoded.trace_id == "tr_1"
    with pytest.raises(ValueError, match="filters"):
        decode_cursor(cursor, TraceListParams(status="error", name="agent"))
    for invalid in ("not-base64!", "x" * 1025):
        with pytest.raises(ValueError):
            decode_cursor(invalid, params)


def test_ingest_list_filter_page_and_detail_round_trip() -> None:
    base = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
    traces: list[dict[str, object]] = []
    for index in range(5):
        payload = sdk_trace_payload(f"workflow-{index}")
        payload["trace_id"] = f"tr_query_{index}"
        payload["start_time"] = (base + timedelta(minutes=index)).isoformat()
        payload["end_time"] = (base + timedelta(minutes=index, seconds=1)).isoformat()
        payload["duration_ms"] = float(index + 1)
        for span_index, span in enumerate(payload["spans"]):
            span["trace_id"] = payload["trace_id"]
            span["span_id"] = f"sp_query_{index}_{span_index}"
            span["start_time"] = payload["start_time"]
            span["end_time"] = payload["end_time"]
        payload["spans"][0]["parent_span_id"] = None
        payload["spans"][1]["parent_span_id"] = payload["spans"][0]["span_id"]
        payload["spans"][2]["parent_span_id"] = payload["spans"][0]["span_id"]
        traces.append(payload)

    same_time = deepcopy(traces[-1])
    same_time["trace_id"] = "tr_query_z"
    same_time["name"] = "workflow-error"
    same_time["status"] = "error"
    same_time["error"] = {"type": "RuntimeError", "message": "failed"}
    for span_index, span in enumerate(same_time["spans"]):
        span["trace_id"] = same_time["trace_id"]
        span["span_id"] = f"sp_query_z_{span_index}"
    same_time["spans"][0]["parent_span_id"] = None
    same_time["spans"][1]["parent_span_id"] = same_time["spans"][0]["span_id"]
    same_time["spans"][2]["parent_span_id"] = same_time["spans"][0]["span_id"]
    same_time["spans"][2]["status"] = "error"
    same_time["spans"][2]["error"] = {"type": "ToolError", "message": "failed"}
    same_time["spans"] = [same_time["spans"][1], same_time["spans"][2], same_time["spans"][0]]
    traces.append(same_time)

    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(*traces)).status_code == 202
        first = client.get("/api/v1/traces", params={"page_size": 2})
        second = client.get(
            "/api/v1/traces", params={"page_size": 2, "cursor": first.json()["next_cursor"]}
        )
        filtered = client.get(
            "/api/v1/traces",
            params={"status": "error", "has_error": True, "span_kind": "tool"},
        )
        by_name = client.get("/api/v1/traces", params={"name": "WORKFLOW-1"})
        by_time = client.get(
            "/api/v1/traces",
            params={
                "started_after": (base + timedelta(minutes=2)).isoformat(),
                "started_before": (base + timedelta(minutes=3)).isoformat(),
            },
        )
        by_duration = client.get(
            "/api/v1/traces", params={"min_duration_ms": 3, "max_duration_ms": 4}
        )
        without_error = client.get("/api/v1/traces", params={"has_error": False})
        llm_traces = client.get("/api/v1/traces", params={"span_kind": "llm"})
        mismatch = client.get(
            "/api/v1/traces",
            params={"cursor": first.json()["next_cursor"], "status": "error"},
        )
        malformed = client.get("/api/v1/traces", params={"cursor": "bad!"})
        detail = client.get(f"/api/v1/traces/{same_time['trace_id']}")
        missing = client.get("/api/v1/traces/tr_missing")
        invalid_query = client.get("/api/v1/traces", params={"page_size": 101})

        statements: list[str] = []

        def count_query(
            _connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", count_query)
        try:
            assert client.get("/api/v1/traces", params={"page_size": 3}).status_code == 200
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count_query)

    assert first.status_code == second.status_code == 200
    first_ids = [item["trace_id"] for item in first.json()["items"]]
    second_ids = [item["trace_id"] for item in second.json()["items"]]
    assert first_ids == ["tr_query_z", "tr_query_4"]
    assert not set(first_ids) & set(second_ids)
    all_ids: list[str] = []
    cursor = None
    with TestClient(create_app()) as client:
        while True:
            response = client.get(
                "/api/v1/traces",
                params={"page_size": 2, **({"cursor": cursor} if cursor else {})},
            ).json()
            all_ids.extend(item["trace_id"] for item in response["items"])
            cursor = response["next_cursor"]
            if cursor is None:
                break
    assert all_ids == [
        "tr_query_z",
        "tr_query_4",
        "tr_query_3",
        "tr_query_2",
        "tr_query_1",
        "tr_query_0",
    ]
    assert len(all_ids) == len(set(all_ids))
    assert filtered.json()["items"][0]["trace_id"] == "tr_query_z"
    summary = filtered.json()["items"][0]
    assert (summary["span_count"], summary["error_span_count"]) == (3, 1)
    assert (summary["llm_span_count"], summary["tool_span_count"]) == (1, 1)
    assert summary["total_input_tokens"] == 3
    assert summary["total_output_tokens"] == 2
    assert summary["total_tokens"] == 5
    assert [item["trace_id"] for item in by_name.json()["items"]] == ["tr_query_1"]
    assert [item["trace_id"] for item in by_time.json()["items"]] == ["tr_query_3", "tr_query_2"]
    assert [item["trace_id"] for item in by_duration.json()["items"]] == [
        "tr_query_3",
        "tr_query_2",
    ]
    assert len(without_error.json()["items"]) == 5
    assert len(llm_traces.json()["items"]) == 6
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == "CURSOR_FILTER_MISMATCH"
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "INVALID_CURSOR"
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["spans"]) == 3
    positions = {span["span_id"]: index for index, span in enumerate(body["spans"])}
    assert all(
        span["parent_span_id"] is None
        or positions[span["parent_span_id"]] < positions[span["span_id"]]
        for span in body["spans"]
    )
    assert body["spans"][1]["llm"]["token_usage"]["total_tokens"] == 5
    assert body["spans"][1]["events"][0]["name"] == "first-token"
    assert body["spans"][2]["output"] == {"temperature_c": 21}
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "TRACE_NOT_FOUND"
    assert invalid_query.status_code == 422
    assert len(statements) == 1


def test_token_summaries_distinguish_unknown_from_measured_zero() -> None:
    unknown = sdk_trace_payload("unknown-usage")
    measured_zero = sdk_trace_payload("measured-zero")
    measured_zero["trace_id"] = "tr_measured_zero"
    for index, span in enumerate(measured_zero["spans"]):
        span["trace_id"] = measured_zero["trace_id"]
        span["span_id"] = f"sp_measured_zero_{index}"
    measured_zero["spans"][0]["parent_span_id"] = None
    measured_zero["spans"][1]["parent_span_id"] = measured_zero["spans"][0]["span_id"]
    measured_zero["spans"][2]["parent_span_id"] = measured_zero["spans"][0]["span_id"]
    unknown["spans"][1]["llm"]["token_usage"] = None
    measured_zero["spans"][1]["llm"]["token_usage"] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }

    with TestClient(create_app()) as client:
        response = client.post("/api/v1/traces", json=envelope(unknown, measured_zero))
        assert response.status_code == 202
        summaries = {
            item["trace_id"]: item for item in client.get("/api/v1/traces").json()["items"]
        }

    assert summaries[unknown["trace_id"]]["total_input_tokens"] is None
    assert summaries[unknown["trace_id"]]["total_output_tokens"] is None
    assert summaries[unknown["trace_id"]]["total_tokens"] is None
    assert summaries[measured_zero["trace_id"]]["total_input_tokens"] == 0
    assert summaries[measured_zero["trace_id"]]["total_output_tokens"] == 0
    assert summaries[measured_zero["trace_id"]]["total_tokens"] == 0


def test_pagination_boundaries_filters_and_ingestion_between_pages() -> None:
    started_at = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    traces = [
        retarget_trace(
            sdk_trace_payload(),
            f"tr_page_{index:03d}",
            started_at=started_at,
            name="keep" if index % 2 == 0 else "discard",
        )
        for index in range(105)
    ]
    with TestClient(create_app()) as client:
        for offset in range(0, len(traces), 10):
            response = client.post("/api/v1/traces", json=envelope(*traces[offset : offset + 10]))
            assert response.status_code == 202

        maximum = client.get("/api/v1/traces", params={"page_size": 100}).json()
        final = client.get(
            "/api/v1/traces",
            params={"page_size": 100, "cursor": maximum["next_cursor"]},
        ).json()
        first_one = client.get("/api/v1/traces", params={"page_size": 1}).json()
        second_one = client.get(
            "/api/v1/traces",
            params={"page_size": 1, "cursor": first_one["next_cursor"]},
        ).json()

        newer = retarget_trace(
            sdk_trace_payload(),
            "tr_page_newer",
            started_at=started_at + timedelta(days=1),
        )
        older = retarget_trace(
            sdk_trace_payload(),
            "tr_page_older",
            started_at=started_at - timedelta(days=1),
        )
        assert client.post("/api/v1/traces", json=envelope(newer, older)).status_code == 202

        seen = [item["trace_id"] for item in first_one["items"]]
        cursor = first_one["next_cursor"]
        while cursor:
            page = client.get("/api/v1/traces", params={"page_size": 7, "cursor": cursor}).json()
            seen.extend(item["trace_id"] for item in page["items"])
            cursor = page["next_cursor"]

        filtered: list[str] = []
        cursor = None
        while True:
            params = {
                "page_size": 7,
                "name": "KEEP",
                "status": "success",
                "span_kind": "llm",
                **({"cursor": cursor} if cursor else {}),
            }
            page = client.get("/api/v1/traces", params=params).json()
            filtered.extend(item["trace_id"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break

    assert len(maximum["items"]) == 100
    assert maximum["has_more"] is True
    assert [item["trace_id"] for item in final["items"]] == [
        "tr_page_004",
        "tr_page_003",
        "tr_page_002",
        "tr_page_001",
        "tr_page_000",
    ]
    assert final["has_more"] is False
    assert first_one["items"][0]["trace_id"] == "tr_page_104"
    assert second_one["items"][0]["trace_id"] == "tr_page_103"
    assert len(seen) == len(set(seen)) == 106
    assert "tr_page_newer" not in seen
    assert "tr_page_older" in seen
    assert len(filtered) == 53
    assert all(int(trace_id.rsplit("_", 1)[1]) % 2 == 0 for trace_id in filtered)


def test_large_detail_is_parent_first_with_deep_and_wide_branches() -> None:
    payload = retarget_trace(
        sdk_trace_payload(),
        "tr_large_detail",
        started_at=datetime(2026, 9, 17, 13, 0, tzinfo=UTC),
        name="多層 workflow " + "x" * 480,
    )
    template = deepcopy(payload["spans"][0])
    spans: list[dict[str, Any]] = []
    for index in range(600):
        span = deepcopy(template)
        span.update(
            span_id=f"sp_large_{index:03d}",
            parent_span_id=(
                None
                if index in (0, 300)
                else f"sp_large_{index - 1:03d}"
                if index < 300
                else "sp_large_300"
            ),
            name=f"operation {index} ✓",
            kind=("llm", "tool", "custom")[index % 3],
            input=None if index % 2 else {"index": index},
            output={"nested": {"values": [index, None, True]}},
            metadata={},
            attributes={"unicode": "東京", "index": index},
            events=(
                [
                    {
                        "name": "first",
                        "timestamp": payload["start_time"],
                        "attributes": {},
                    },
                    {
                        "name": "second",
                        "timestamp": payload["end_time"],
                        "attributes": {"ok": True},
                    },
                ]
                if index == 599
                else []
            ),
            llm=None,
        )
        spans.append(span)
    payload["spans"] = list(reversed(spans))

    with TestClient(create_app()) as client:
        accepted = client.post("/api/v1/traces", json=envelope(payload))
        detail = client.get("/api/v1/traces/tr_large_detail")
        summary = client.get("/api/v1/traces").json()["items"][0]

    assert accepted.status_code == 202
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["spans"]) == 600
    positions = {span["span_id"]: index for index, span in enumerate(body["spans"])}
    assert all(
        span["parent_span_id"] is None
        or positions[span["parent_span_id"]] < positions[span["span_id"]]
        for span in body["spans"]
    )
    assert [span["span_id"] for span in body["spans"] if span["parent_span_id"] is None] == [
        "sp_large_000",
        "sp_large_300",
    ]
    assert body["spans"][-1]["events"][1]["attributes"] == {"ok": True}
    assert summary["span_count"] == 600
    assert summary["llm_span_count"] == 200
    assert summary["tool_span_count"] == 200


def test_query_error_does_not_expose_database_details(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentscope_api.api.routes import traces as trace_routes

    async def fail(*_: object) -> None:
        raise SQLAlchemyError("password=secret C:\\private\\database SELECT * FROM traces")

    monkeypatch.setattr(trace_routes, "list_traces", fail)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/traces")

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "QUERY_ERROR", "message": "trace query failed"}}
    assert "secret" not in response.text
    assert "SELECT" not in response.text
