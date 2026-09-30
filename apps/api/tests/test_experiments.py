from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_trace_ingestion import envelope, sdk_trace_payload

from agentscope_api.database import SessionLocal
from agentscope_api.main import create_app
from agentscope_api.models.evaluation import EvaluationDefinitionRecord
from agentscope_api.schemas.experiments import ExperimentConfigure
from agentscope_api.services.experiments import configure_experiment, mark_experiment_ready

pytestmark = pytest.mark.usefixtures("clean_database")


def _trace(client: TestClient, name: str) -> str:
    trace = sdk_trace_payload(name)
    response = client.post("/api/v1/traces", json=envelope(trace))
    assert response.status_code == 202, response.text
    return str(trace["trace_id"])


def _definition(client: TestClient) -> str:
    response = client.post(
        "/api/v1/evaluation-definitions",
        json={
            "name": "Experiment exact match",
            "evaluator_kind": "exact_match",
            "evaluator_config": {"expected": "Paris", "case_sensitive": False},
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _configuration(a_trace_id: str, b_trace_id: str, definition_id: str) -> dict[str, object]:
    return {
        "name": "Answer quality A/B",
        "description": "Same logical case, distinct variant executions.",
        "variants": [
            {
                "key": "A",
                "name": "Current agent",
                "provenance": {"agent_version": "1.0", "metadata": {"region": "test"}},
            },
            {
                "key": "B",
                "name": "Candidate agent",
                "provenance": {"agent_version": "1.1", "model": "test-model"},
            },
        ],
        "subjects": [{"a_trace_id": a_trace_id, "b_trace_id": b_trace_id}],
        "evaluation_definition_ids": [definition_id],
    }


def test_experiment_create_configure_read_list_and_ready_snapshot() -> None:
    with TestClient(create_app()) as client:
        a_trace_id = _trace(client, "case 1 variant A")
        b_trace_id = _trace(client, "case 1 variant B")
        second_a_trace_id = _trace(client, "case 2 variant A")
        second_b_trace_id = _trace(client, "case 2 variant B")
        definition_id = _definition(client)
        created = client.post(
            "/api/v1/experiments",
            json={"name": "Draft experiment", "description": "Initial draft"},
        )
        assert created.status_code == 201, created.text
        experiment_id = str(created.json()["id"])

        configuration = _configuration(a_trace_id, b_trace_id, definition_id)
        subjects = configuration["subjects"]
        assert isinstance(subjects, list)
        subjects.append(
            {"a_trace_id": second_a_trace_id, "b_trace_id": second_b_trace_id}
        )
        configured = client.post(
            f"/api/v1/experiments/{experiment_id}/configuration",
            json=configuration,
        )
        repeated = client.post(
            f"/api/v1/experiments/{experiment_id}/configuration",
            json=configuration,
        )
        ready = client.post(f"/api/v1/experiments/{experiment_id}/ready")
        repeated_ready = client.post(f"/api/v1/experiments/{experiment_id}/ready")
        detail = client.get(f"/api/v1/experiments/{experiment_id}")
        listing = client.get("/api/v1/experiments", params={"status": "ready", "page_size": 1})

        assert configured.status_code == repeated.status_code == 200
        assert [item["id"] for item in configured.json()["variants"]] == [
            item["id"] for item in repeated.json()["variants"]
        ]
        assert ready.status_code == repeated_ready.status_code == 200
        assert ready.json()["status"] == "ready"

        async def mutate_definition() -> None:
            async with SessionLocal() as session:
                definition = await session.get(
                    EvaluationDefinitionRecord, UUID(definition_id)
                )
                assert definition is not None
                definition.name = "Changed after experiment lock"
                definition.evaluator_config = {"expected": "London", "case_sensitive": True}
                await session.commit()

        asyncio.run(mutate_definition())
        detail = client.get(f"/api/v1/experiments/{experiment_id}")

    assert detail.status_code == 200
    assert [variant["key"] for variant in detail.json()["variants"]] == ["A", "B"]
    assert detail.json()["variants"][0]["provenance"]["metadata"] == {"region": "test"}
    assert detail.json()["subjects"] == [
        {"position": 0, "a_trace_id": a_trace_id, "b_trace_id": b_trace_id},
        {
            "position": 1,
            "a_trace_id": second_a_trace_id,
            "b_trace_id": second_b_trace_id,
        },
    ]
    assert detail.json()["evaluation_conditions"][0]["definition_id"] == definition_id
    assert detail.json()["evaluation_conditions"][0]["definition_name"] == "Experiment exact match"
    assert detail.json()["evaluation_conditions"][0]["evaluator_config"] == {
        "expected": "Paris",
        "case_sensitive": False,
    }
    assert listing.status_code == 200
    assert listing.json()["items"][0]["subject_count"] == 2
    assert listing.json()["has_more"] is False


def test_experiment_validation_references_and_lock_immutability() -> None:
    with pytest.raises(ValidationError):
        ExperimentConfigure.model_validate(
            _configuration("same", "same", "00000000-0000-0000-0000-000000000001")
        )
    oversized = _configuration("a", "b", "00000000-0000-0000-0000-000000000001")
    variants = oversized["variants"]
    assert isinstance(variants, list)
    variants[0]["provenance"] = {"metadata": {"value": "x" * 17_000}}
    with pytest.raises(ValidationError):
        ExperimentConfigure.model_validate(oversized)
    missing_b = _configuration("a", "b", "00000000-0000-0000-0000-000000000001")
    missing_b["variants"] = [missing_b["variants"][0], missing_b["variants"][0]]
    with pytest.raises(ValidationError):
        ExperimentConfigure.model_validate(missing_b)
    duplicate_membership = _configuration(
        "a", "b", "00000000-0000-0000-0000-000000000001"
    )
    duplicate_membership["subjects"] = [
        {"a_trace_id": "a", "b_trace_id": "b"},
        {"a_trace_id": "a", "b_trace_id": "c"},
    ]
    with pytest.raises(ValidationError):
        ExperimentConfigure.model_validate(duplicate_membership)

    with TestClient(create_app()) as client:
        a_trace_id = _trace(client, "immutable A")
        b_trace_id = _trace(client, "immutable B")
        definition_id = _definition(client)
        experiment = client.post("/api/v1/experiments", json={"name": "Immutable"}).json()
        experiment_id = str(experiment["id"])
        missing_trace = client.post(
            f"/api/v1/experiments/{experiment_id}/configuration",
            json=_configuration(a_trace_id, "missing-trace", definition_id),
        )
        missing_definition_payload = _configuration(a_trace_id, b_trace_id, definition_id)
        missing_definition_payload["evaluation_definition_ids"] = [
            "00000000-0000-0000-0000-000000000000"
        ]
        missing_definition = client.post(
            f"/api/v1/experiments/{experiment_id}/configuration",
            json=missing_definition_payload,
        )
        configured = client.post(
            f"/api/v1/experiments/{experiment_id}/configuration",
            json=_configuration(a_trace_id, b_trace_id, definition_id),
        )
        assert configured.status_code == 200
        assert client.post(f"/api/v1/experiments/{experiment_id}/ready").status_code == 200
        mutation = client.post(
            f"/api/v1/experiments/{experiment_id}/configuration",
            json=_configuration(a_trace_id, b_trace_id, definition_id),
        )

    assert missing_trace.status_code == 404
    assert missing_trace.json()["error"]["code"] == "TRACE_NOT_FOUND"
    assert missing_definition.status_code == 404
    assert missing_definition.json()["error"]["code"] == "EVALUATION_DEFINITION_NOT_FOUND"
    assert mutation.status_code == 409
    assert mutation.json()["error"]["code"] == "EXPERIMENT_IMMUTABLE"


def test_incomplete_experiments_cannot_become_ready_and_pagination_is_bounded() -> None:
    with TestClient(create_app()) as client:
        experiment = client.post("/api/v1/experiments", json={"name": "Incomplete"}).json()
        rejected = client.post(f"/api/v1/experiments/{experiment['id']}/ready")
        invalid_transition = client.get("/api/v1/experiments", params={"status": "unknown"})
        invalid_page = client.get("/api/v1/experiments", params={"page_size": 101})
        missing = client.get("/api/v1/experiments/00000000-0000-0000-0000-000000000000")

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "EXPERIMENT_INCOMPLETE"
    assert invalid_transition.status_code == invalid_page.status_code == 422
    assert missing.status_code == 404


def test_concurrent_ready_transition_is_idempotent() -> None:
    with TestClient(create_app()) as client:
        a_trace_id = _trace(client, "concurrent A")
        b_trace_id = _trace(client, "concurrent B")
        definition_id = _definition(client)
        experiment = client.post("/api/v1/experiments", json={"name": "Concurrent"}).json()
        experiment_id = UUID(str(experiment["id"]))

    payload = ExperimentConfigure.model_validate(
        _configuration(a_trace_id, b_trace_id, definition_id)
    )

    async def prepare() -> None:
        async with SessionLocal() as session:
            await configure_experiment(session, experiment_id, payload)

    async def ready() -> None:
        async with SessionLocal() as session:
            await mark_experiment_ready(session, experiment_id)

    async def exercise() -> None:
        await prepare()
        await asyncio.gather(ready(), ready())

    asyncio.run(exercise())
    with TestClient(create_app()) as client:
        assert client.get(f"/api/v1/experiments/{experiment_id}").json()["status"] == "ready"
