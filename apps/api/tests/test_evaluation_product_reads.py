from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from test_trace_ingestion import envelope, sdk_trace_payload

from agentscope_api.database import SessionLocal, engine
from agentscope_api.main import create_app
from agentscope_api.models.evaluation import EvaluationRunRecord
from agentscope_api.services.evaluation_execution import transition_run
from agentscope_api.services.evaluation_orchestration import (
    ClaimOutcome,
    ProcessOutcome,
    claim_evaluation_run,
    process_evaluation_message,
)

pytestmark = pytest.mark.usefixtures("clean_database")


def _trace(trace_id: str, output: object, *, name: str | None = None) -> dict[str, object]:
    payload = deepcopy(sdk_trace_payload(name or trace_id))
    payload["trace_id"] = trace_id
    payload["name"] = name or trace_id
    payload["output"] = output
    span_ids = {
        span["span_id"]: f"sp_{trace_id}_{index}" for index, span in enumerate(payload["spans"])
    }
    for span in payload["spans"]:
        span["trace_id"] = trace_id
        span["span_id"] = span_ids[span["span_id"]]
        span["parent_span_id"] = span_ids.get(span["parent_span_id"])
    return payload


def _definition(client: TestClient, expected: object = "Paris") -> dict[str, object]:
    response = client.post(
        "/api/v1/evaluation-definitions",
        json={
            "name": "Product summary definition",
            "evaluator_kind": "exact_match",
            "evaluator_config": {"expected": expected, "case_sensitive": True},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _run(client: TestClient, definition: dict[str, object]) -> dict[str, object]:
    response = client.post("/api/v1/evaluation-runs", json={"definition_id": definition["id"]})
    assert response.status_code == 201, response.text
    return response.json()


def _submit(client: TestClient, run_id: object, trace_ids: list[str]) -> None:
    with patch("agentscope_api.api.routes.evaluations.enqueue_evaluation_run"):
        response = client.post(
            f"/api/v1/evaluation-runs/{run_id}/execute", json={"trace_ids": trace_ids}
        )
    assert response.status_code == 202, response.text


def _process(run_id: object) -> None:
    assert asyncio.run(process_evaluation_message(UUID(str(run_id)))) is ProcessOutcome.COMPLETED


def test_run_summary_reports_zero_and_durable_queued_progress_in_one_query() -> None:
    traces = [_trace("tr_summary_1", "Paris"), _trace("tr_summary_2", "London")]
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(*traces)).status_code == 202
        run = _run(client, _definition(client))
        pending = client.get(f"/api/v1/evaluation-runs/{run['id']}")
        _submit(client, run["id"], [trace["trace_id"] for trace in traces])

        statements: list[str] = []

        def count_query(*args: object) -> None:
            statements.append(str(args[2]))

        event.listen(engine.sync_engine, "before_cursor_execute", count_query)
        try:
            queued = client.get(f"/api/v1/evaluation-runs/{run['id']}")
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count_query)

    assert pending.status_code == queued.status_code == 200
    assert (
        pending.json()
        | {
            "subject_count": 0,
            "result_count": 0,
            "passed_count": 0,
            "failed_count": 0,
            "error_count": 0,
            "scored_count": 0,
            "average_score": None,
        }
        == pending.json()
    )
    body = queued.json()
    assert body["status"] == "queued"
    assert body["queued_at"] is not None
    assert body["subject_count"] == 2
    assert body["result_count"] == 0
    assert body["average_score"] is None
    assert len(statements) == 1


def test_completed_summary_filters_paginated_results_and_joins_trace_names() -> None:
    traces = [
        _trace("tr_summary_pass", "Paris", name="Passing agent"),
        _trace("tr_summary_fail", "London", name="Failing agent"),
        _trace("tr_summary_error", None, name="Unavailable output agent"),
    ]
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(*traces)).status_code == 202
        run = _run(client, _definition(client))
        _submit(client, run["id"], [trace["trace_id"] for trace in traces])
    _process(run["id"])

    with TestClient(create_app()) as client:
        detail = client.get(f"/api/v1/evaluation-runs/{run['id']}")
        listing = client.get("/api/v1/evaluation-runs", params={"page_size": 10})
        errors = client.get(
            f"/api/v1/evaluation-runs/{run['id']}/results",
            params={"outcome": "error"},
        )
        invalid_filter = client.get(
            f"/api/v1/evaluation-runs/{run['id']}/results",
            params={"outcome": "unknown"},
        )
        first = client.get(
            f"/api/v1/evaluation-runs/{run['id']}/results",
            params={"page_size": 2, "offset": 0},
        ).json()
        repeated = client.get(
            f"/api/v1/evaluation-runs/{run['id']}/results",
            params={"page_size": 2, "offset": 0},
        ).json()
        second = client.get(
            f"/api/v1/evaluation-runs/{run['id']}/results",
            params={"page_size": 2, "offset": 2},
        ).json()

    assert detail.status_code == listing.status_code == errors.status_code == 200
    summary = detail.json()
    assert summary["status"] == "completed"
    assert summary["subject_count"] == summary["result_count"] == 3
    assert (summary["passed_count"], summary["failed_count"], summary["error_count"]) == (
        1,
        1,
        1,
    )
    assert summary["scored_count"] == 2
    assert summary["average_score"] == pytest.approx(0.5)
    assert listing.json()["items"][0] == summary
    assert [item["trace_name"] for item in errors.json()["items"]] == ["Unavailable output agent"]
    assert invalid_filter.status_code == 422
    assert first == repeated
    assert first["has_more"] is True
    assert second["has_more"] is False
    result_ids = [item["id"] for item in first["items"] + second["items"]]
    assert len(result_ids) == len(set(result_ids)) == 3


@pytest.mark.parametrize(
    ("outputs", "passed", "errors", "scored", "average"),
    [
        (["Paris", "Paris"], 2, 0, 2, 1.0),
        ([None, None], 0, 2, 0, None),
    ],
)
def test_average_score_uses_only_scored_results(
    outputs: list[object], passed: int, errors: int, scored: int, average: float | None
) -> None:
    traces = [_trace(f"tr_score_{index}", output) for index, output in enumerate(outputs)]
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(*traces)).status_code == 202
        run = _run(client, _definition(client))
        _submit(client, run["id"], [trace["trace_id"] for trace in traces])
    _process(run["id"])

    with TestClient(create_app()) as client:
        summary = client.get(f"/api/v1/evaluation-runs/{run['id']}").json()

    assert summary["passed_count"] == passed
    assert summary["error_count"] == errors
    assert summary["scored_count"] == scored
    assert summary["average_score"] == average


def test_running_and_failed_summaries_keep_accurate_durable_counts() -> None:
    trace = _trace("tr_running_summary", "Paris")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        run = _run(client, _definition(client))
        _submit(client, run["id"], [trace["trace_id"]])

    async def claim_and_fail() -> None:
        run_id = UUID(str(run["id"]))
        async with SessionLocal() as session:
            claim = await claim_evaluation_run(session, run_id)
        assert claim.outcome is ClaimOutcome.CLAIMED

        with TestClient(create_app()) as client:
            running = client.get(f"/api/v1/evaluation-runs/{run_id}").json()
        assert running["status"] == "running"
        assert running["subject_count"] == 1
        assert running["result_count"] == 0

        async with SessionLocal() as session, session.begin():
            record = await session.get(EvaluationRunRecord, run_id, with_for_update=True)
            assert record is not None
            transition_run(record, "failed", error_message="safe evaluation failure")

    asyncio.run(claim_and_fail())
    with TestClient(create_app()) as client:
        failed = client.get(f"/api/v1/evaluation-runs/{run['id']}").json()
    assert failed["status"] == "failed"
    assert failed["subject_count"] == 1
    assert failed["result_count"] == 0
    assert failed["error_message"] == "safe evaluation failure"


def test_trace_summaries_expose_availability_without_exposing_output() -> None:
    available = _trace("tr_output_available", {"answer": "private"})
    unavailable = _trace("tr_output_unavailable", None)
    with TestClient(create_app()) as client:
        accepted = client.post("/api/v1/traces", json=envelope(available, unavailable))
        assert accepted.status_code == 202
        items = client.get("/api/v1/traces", params={"page_size": 10}).json()["items"]

    by_id = {item["trace_id"]: item for item in items}
    assert by_id["tr_output_available"]["output_available"] is True
    assert by_id["tr_output_unavailable"]["output_available"] is False
    assert all("output" not in item for item in items)
