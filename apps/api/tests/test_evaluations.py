from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_trace_ingestion import envelope, sdk_trace_payload

from agentscope_api.database import SessionLocal
from agentscope_api.main import create_app
from agentscope_api.models.evaluation import EvaluationDefinitionRecord, EvaluationRunRecord
from agentscope_api.schemas.evaluations import (
    EvaluationResultCreate,
    EvaluationRunStatus,
)
from agentscope_api.services.evaluations import (
    EvaluationRunNotFound,
    InvalidEvaluationRunState,
    TraceNotFound,
    create_result,
)

pytestmark = pytest.mark.usefixtures("clean_database")


def _definition(
    client: TestClient,
    *,
    name: str = "Exact answer",
    kind: str = "exact_match",
    config: dict[str, object] | None = None,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/evaluation-definitions",
        json={
            "name": name,
            "description": "Checks the final answer without executing it yet.",
            "evaluator_kind": kind,
            "evaluator_config": config or {"expected": "Paris", "case_sensitive": False},
            "is_enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_definition_create_get_list_filter_and_validation() -> None:
    too_deep: object = "leaf"
    for _ in range(18):
        too_deep = {"child": too_deep}
    with TestClient(create_app()) as client:
        first = _definition(client, name="First")
        second = _definition(
            client,
            name="Second",
            kind="contains",
            config={"substring": "approved", "case_sensitive": True},
        )
        detail = client.get(f"/api/v1/evaluation-definitions/{first['id']}")
        listing = client.get("/api/v1/evaluation-definitions", params={"page_size": 1})
        filtered = client.get(
            "/api/v1/evaluation-definitions",
            params={"evaluator_kind": "contains", "is_enabled": True},
        )
        missing = client.get("/api/v1/evaluation-definitions/00000000-0000-0000-0000-000000000000")
        malformed = client.get("/api/v1/evaluation-definitions/not-a-uuid")
        unknown = client.post(
            "/api/v1/evaluation-definitions",
            json={"name": "bad", "evaluator_kind": "magic", "evaluator_config": {}},
        )
        invalid_config = client.post(
            "/api/v1/evaluation-definitions",
            json={
                "name": "bad config",
                "evaluator_kind": "contains",
                "evaluator_config": {"substring": ""},
            },
        )
        too_long = client.post(
            "/api/v1/evaluation-definitions",
            json={
                "name": "x" * 201,
                "evaluator_kind": "exact_match",
                "evaluator_config": {"expected": "ok"},
            },
        )
        oversized_config = client.post(
            "/api/v1/evaluation-definitions",
            json={
                "name": "oversized",
                "evaluator_kind": "exact_match",
                "evaluator_config": {"expected": "x" * 33_000},
            },
        )
        deep_config = client.post(
            "/api/v1/evaluation-definitions",
            json={
                "name": "deep",
                "evaluator_kind": "exact_match",
                "evaluator_config": {"expected": too_deep},
            },
        )

    assert detail.status_code == 200
    assert detail.json()["evaluator_config"] == {"expected": "Paris", "case_sensitive": False}
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [second["id"]]
    assert listing.json()["has_more"] is True
    assert [item["id"] for item in filtered.json()["items"]] == [second["id"]]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "EVALUATION_DEFINITION_NOT_FOUND"
    assert malformed.status_code == 422
    assert all(
        response.status_code == 422
        for response in (unknown, invalid_config, too_long, oversized_config, deep_config)
    )
    assert all(
        response.json()["error"]["code"] == "VALIDATION_ERROR"
        for response in (
            malformed,
            unknown,
            invalid_config,
            too_long,
            oversized_config,
            deep_config,
        )
    )


def test_run_creation_snapshots_definition_and_supports_bounded_filters() -> None:
    with TestClient(create_app()) as client:
        definition = _definition(client)
        created = client.post(
            "/api/v1/evaluation-runs",
            json={"definition_id": definition["id"]},
        )
        assert created.status_code == 201
        run = created.json()
        assert run["status"] == "pending"
        assert run["definition_name"] == definition["name"]
        assert run["evaluator_kind"] == definition["evaluator_kind"]
        assert run["evaluator_config"] == definition["evaluator_config"]

        async def mutate_definition() -> None:
            async with SessionLocal() as session:
                record = await session.get(EvaluationDefinitionRecord, UUID(str(definition["id"])))
                assert record is not None
                record.name = "Changed later"
                record.evaluator_config = {"expected": "London", "case_sensitive": True}
                record.is_enabled = False
                await session.commit()

        asyncio.run(mutate_definition())
        detail = client.get(f"/api/v1/evaluation-runs/{run['id']}")
        listing = client.get(
            "/api/v1/evaluation-runs",
            params={"definition_id": definition["id"], "status": "pending", "page_size": 1},
        )
        disabled = client.post("/api/v1/evaluation-runs", json={"definition_id": definition["id"]})
        missing_definition = client.post(
            "/api/v1/evaluation-runs",
            json={"definition_id": "00000000-0000-0000-0000-000000000000"},
        )
        invalid_status = client.get("/api/v1/evaluation-runs", params={"status": "finished"})
        invalid_page = client.get("/api/v1/evaluation-runs", params={"page_size": 101})

    assert detail.status_code == 200
    assert detail.json()["definition_name"] == definition["name"]
    assert detail.json()["evaluator_config"] == definition["evaluator_config"]
    assert [item["id"] for item in listing.json()["items"]] == [run["id"]]
    assert missing_definition.status_code == 404
    assert missing_definition.json()["error"]["code"] == "EVALUATION_DEFINITION_NOT_FOUND"
    assert disabled.status_code == 409
    assert disabled.json()["error"]["code"] == "EVALUATION_DEFINITION_DISABLED"
    assert invalid_status.status_code == invalid_page.status_code == 422


def test_internal_result_creation_and_public_reads_preserve_trace_linkage() -> None:
    trace = sdk_trace_payload("evaluated trace")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        definition = _definition(client)
        run = client.post(
            "/api/v1/evaluation-runs", json={"definition_id": definition["id"]}
        ).json()

    async def persist_result() -> UUID:
        async with SessionLocal() as session:
            record = await session.get(EvaluationRunRecord, UUID(str(run["id"])))
            assert record is not None
            record.status = EvaluationRunStatus.RUNNING.value
            record.started_at = datetime.now(UTC)
            await session.commit()
        async with SessionLocal() as session:
            result = await create_result(
                session,
                EvaluationResultCreate(
                    run_id=UUID(str(run["id"])),
                    trace_id=trace["trace_id"],
                    outcome="passed",
                    score=0.875,
                    details={"rule": "exact_match", "observed": "Paris"},
                ),
            )
            result_id = result.id
        async with SessionLocal() as session:
            with pytest.raises(IntegrityError):
                await create_result(
                    session,
                    EvaluationResultCreate(
                        run_id=UUID(str(run["id"])),
                        trace_id=trace["trace_id"],
                        outcome="passed",
                        score=0.875,
                    ),
                )
        return result_id

    result_id = asyncio.run(persist_result())
    with TestClient(create_app()) as client:
        detail = client.get(f"/api/v1/evaluation-results/{result_id}")
        listing = client.get(
            f"/api/v1/evaluation-runs/{run['id']}/results",
            params={"trace_id": trace["trace_id"]},
        )
        missing_run = client.get(
            "/api/v1/evaluation-runs/00000000-0000-0000-0000-000000000000/results"
        )
        public_write = client.post(
            "/api/v1/evaluation-results",
            json={"run_id": run["id"], "trace_id": trace["trace_id"], "outcome": "passed"},
        )

    assert detail.status_code == 200
    assert detail.json()["trace_id"] == trace["trace_id"]
    assert detail.json()["score"] == 0.875
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [str(result_id)]
    assert missing_run.status_code == 404
    assert missing_run.json()["error"]["code"] == "EVALUATION_RUN_NOT_FOUND"
    assert public_write.status_code == 404


def test_result_service_rejects_invalid_references_state_scores_and_duplicates() -> None:
    with pytest.raises(ValidationError):
        EvaluationResultCreate(
            run_id=UUID(int=1), trace_id="trace", outcome="passed", score=float("nan")
        )
    with pytest.raises(ValidationError):
        EvaluationResultCreate(run_id=UUID(int=1), trace_id="trace", outcome="error", score=0.5)

    async def verify() -> None:
        async with SessionLocal() as session:
            with pytest.raises(EvaluationRunNotFound):
                await create_result(
                    session,
                    EvaluationResultCreate(
                        run_id=UUID(int=1), trace_id="trace", outcome="failed", score=0.0
                    ),
                )

        async with SessionLocal() as session:
            definitions = (await session.scalars(select(EvaluationDefinitionRecord))).all()
            if not definitions:
                definition = EvaluationDefinitionRecord(
                    name="service test",
                    description=None,
                    evaluator_kind="exact_match",
                    evaluator_config={"expected": True, "case_sensitive": True},
                    is_enabled=True,
                )
                session.add(definition)
                await session.flush()
            else:
                definition = definitions[0]
            run = EvaluationRunRecord(
                definition_id=definition.id,
                definition_name=definition.name,
                evaluator_kind=definition.evaluator_kind,
                evaluator_config=definition.evaluator_config,
                status="pending",
            )
            session.add(run)
            await session.commit()
            run_id = run.id

        async with SessionLocal() as session:
            with pytest.raises(InvalidEvaluationRunState):
                await create_result(
                    session,
                    EvaluationResultCreate(
                        run_id=run_id,
                        trace_id="missing-trace",
                        outcome="failed",
                        score=0.0,
                    ),
                )

        async with SessionLocal() as session:
            run = await session.get(EvaluationRunRecord, run_id)
            assert run is not None
            run.status = "running"
            run.started_at = datetime.now(UTC)
            await session.commit()

        async with SessionLocal() as session:
            with pytest.raises(TraceNotFound):
                await create_result(
                    session,
                    EvaluationResultCreate(
                        run_id=run_id,
                        trace_id="missing-trace",
                        outcome="failed",
                        score=0.0,
                    ),
                )

    asyncio.run(verify())
