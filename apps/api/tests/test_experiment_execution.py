from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_trace_ingestion import envelope, sdk_trace_payload

from agentscope_api.database import SessionLocal
from agentscope_api.jobs.recover import recover_experiments_once
from agentscope_api.main import create_app
from agentscope_api.models.evaluation import EvaluationDefinitionRecord
from agentscope_api.models.experiment import (
    ExperimentEvaluationConditionRecord,
    ExperimentRunRecord,
    ExperimentRunResultRecord,
)
from agentscope_api.schemas.experiment_runs import ExperimentRunStatus
from agentscope_api.services.experiment_execution import (
    ClaimOutcome,
    ExperimentDecision,
    ProcessOutcome,
    claim_experiment_run,
    finish_experiment_run,
    persist_decision_chunk,
    process_experiment_message,
    recover_experiment_runs,
)

pytestmark = pytest.mark.usefixtures("clean_database")


def _trace(client: TestClient, name: str, output: object) -> str:
    trace = sdk_trace_payload(name)
    trace["output"] = output
    response = client.post("/api/v1/traces", json=envelope(trace))
    assert response.status_code == 202, response.text
    return str(trace["trace_id"])


def _definition(
    client: TestClient, name: str, kind: str, config: dict[str, object]
) -> str:
    response = client.post(
        "/api/v1/evaluation-definitions",
        json={"name": name, "evaluator_kind": kind, "evaluator_config": config},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _ready_experiment(client: TestClient) -> tuple[dict[str, object], list[str], list[str]]:
    trace_ids = [
        _trace(client, "subject 0 A", "Paris"),
        _trace(client, "subject 0 B", "Paris approved"),
        _trace(client, "subject 1 A", "London"),
        _trace(client, "subject 1 B", "approved"),
    ]
    definition_ids = [
        _definition(
            client,
            "Exact Paris",
            "exact_match",
            {"expected": "Paris", "case_sensitive": True},
        ),
        _definition(
            client,
            "Contains approved",
            "contains",
            {"substring": "approved", "case_sensitive": True},
        ),
    ]
    experiment = client.post("/api/v1/experiments", json={"name": "Durable A/B"}).json()
    configured = client.post(
        f"/api/v1/experiments/{experiment['id']}/configuration",
        json={
            "name": "Durable A/B",
            "variants": [
                {
                    "key": "A",
                    "name": "Control",
                    "provenance": {
                        "agent_version": "1.0",
                        "metadata": {"lane": "control"},
                    },
                },
                {
                    "key": "B",
                    "name": "Candidate",
                    "provenance": {"agent_version": "1.1", "model": "test-model"},
                },
            ],
            "subjects": [
                {"a_trace_id": trace_ids[0], "b_trace_id": trace_ids[1]},
                {"a_trace_id": trace_ids[2], "b_trace_id": trace_ids[3]},
            ],
            "evaluation_definition_ids": definition_ids,
        },
    )
    assert configured.status_code == 200, configured.text
    ready = client.post(f"/api/v1/experiments/{experiment['id']}/ready")
    assert ready.status_code == 200, ready.text
    return ready.json(), trace_ids, definition_ids


def test_run_creation_requires_ready_and_snapshots_expected_cardinality() -> None:
    with TestClient(create_app()) as client:
        draft = client.post("/api/v1/experiments", json={"name": "Draft"}).json()
        rejected = client.post(f"/api/v1/experiments/{draft['id']}/runs")
        experiment, _, _ = _ready_experiment(client)
        created = client.post(f"/api/v1/experiments/{experiment['id']}/runs")
        listing = client.get(f"/api/v1/experiments/{experiment['id']}/runs")
        detail = client.get(f"/api/v1/experiment-runs/{created.json()['id']}")
        second_active = client.post(f"/api/v1/experiments/{experiment['id']}/runs")

    assert rejected.status_code == 409
    assert created.status_code == 201
    assert created.json()["status"] == "pending"
    assert created.json()["expected_decision_count"] == 8
    assert created.json()["completed_decision_count"] == 0
    assert created.json()["remaining_decision_count"] == 8
    assert listing.status_code == detail.status_code == 200
    assert listing.json()["items"][0]["id"] == created.json()["id"]
    assert second_active.status_code == 409


def test_execution_uses_frozen_conditions_and_returns_ordered_attributed_results() -> None:
    with TestClient(create_app()) as client:
        experiment, trace_ids, definition_ids = _ready_experiment(client)
        run = client.post(f"/api/v1/experiments/{experiment['id']}/runs").json()
        submitted = client.post(f"/api/v1/experiment-runs/{run['id']}/execute")
        repeated = client.post(f"/api/v1/experiment-runs/{run['id']}/execute")
    assert submitted.status_code == repeated.status_code == 202

    async def mutate_and_execute() -> ProcessOutcome:
        async with SessionLocal() as session:
            definition = await session.get(EvaluationDefinitionRecord, UUID(definition_ids[0]))
            assert definition is not None
            definition.evaluator_config = {"expected": "changed", "case_sensitive": True}
            await session.commit()
        return await process_experiment_message(UUID(str(run["id"])))

    assert asyncio.run(mutate_and_execute()) is ProcessOutcome.COMPLETED
    assert asyncio.run(process_experiment_message(UUID(str(run["id"])))) is ProcessOutcome.IGNORED

    with TestClient(create_app()) as client:
        detail = client.get(f"/api/v1/experiment-runs/{run['id']}")
        results = client.get(
            f"/api/v1/experiment-runs/{run['id']}/results", params={"page_size": 8}
        )
        filtered = client.get(
            f"/api/v1/experiment-runs/{run['id']}/results",
            params={"variant": "B", "condition_position": 1, "page_size": 1},
        )

    assert detail.json()["status"] == "completed"
    assert detail.json()["completed_decision_count"] == 8
    assert detail.json()["remaining_decision_count"] == 0
    assert [
        (item["subject_position"], item["variant"], item["condition_position"])
        for item in results.json()["items"]
    ] == [
        (subject, variant, condition)
        for subject in range(2)
        for variant in ("A", "B")
        for condition in range(2)
    ]
    assert [item["trace_id"] for item in results.json()["items"]] == [
        trace_ids[0],
        trace_ids[0],
        trace_ids[1],
        trace_ids[1],
        trace_ids[2],
        trace_ids[2],
        trace_ids[3],
        trace_ids[3],
    ]
    assert results.json()["items"][0]["outcome"] == "passed"
    assert filtered.status_code == 200
    assert filtered.json()["has_more"] is True
    assert filtered.json()["items"][0]["variant"] == "B"


def test_partial_progress_retry_and_stale_attempt_fencing_converge_without_duplicates() -> None:
    with TestClient(create_app()) as client:
        experiment, trace_ids, definition_ids = _ready_experiment(client)
        run = client.post(f"/api/v1/experiments/{experiment['id']}/runs").json()
        assert client.post(f"/api/v1/experiment-runs/{run['id']}/execute").status_code == 202
    run_id = UUID(str(run["id"]))

    async def exercise() -> None:
        async with SessionLocal() as session:
            first = await claim_experiment_run(session, run_id)
        assert first.outcome is ClaimOutcome.CLAIMED and first.token is not None
        decision = ExperimentDecision(
            subject_position=0,
            variant="A",
            condition_position=0,
            trace_id=trace_ids[0],
            definition_id=UUID(definition_ids[0]),
            outcome="passed",
            score=1.0,
            details={"reason_code": "exact_match"},
        )
        assert await persist_decision_chunk(
            run_id, first.token, first.attempt, [decision], now=datetime.now(UTC)
        )
        assert not await finish_experiment_run(run_id, first.token, first.attempt)

        expired_at = datetime.now(UTC) + timedelta(minutes=10)
        async with SessionLocal() as session:
            recovered = await recover_experiment_runs(session, now=expired_at)
        assert recovered == [run_id]
        async with SessionLocal() as session:
            second = await claim_experiment_run(session, run_id, now=expired_at)
        assert second.outcome is ClaimOutcome.CLAIMED and second.token is not None
        assert not await persist_decision_chunk(
            run_id, first.token, first.attempt, [decision], now=expired_at
        )
        assert not await finish_experiment_run(run_id, first.token, first.attempt)

        # Return the current claim to queued so the normal worker path can resume it.
        async with SessionLocal() as session, session.begin():
            record = await session.get(ExperimentRunRecord, run_id, with_for_update=True)
            assert record is not None
            record.status = ExperimentRunStatus.QUEUED.value
            record.lease_token = None
            record.lease_expires_at = None
            record.last_enqueued_at = None

    asyncio.run(exercise())
    assert asyncio.run(process_experiment_message(run_id)) is ProcessOutcome.COMPLETED

    async def verify() -> tuple[int, int]:
        async with SessionLocal() as session:
            results = list(
                await session.scalars(
                    select(ExperimentRunResultRecord).where(
                        ExperimentRunResultRecord.run_id == run_id
                    )
                )
            )
            return len(results), len({
                (result.subject_position, result.variant_key, result.condition_position)
                for result in results
            })

    assert asyncio.run(verify()) == (8, 8)


def test_invalid_snapshot_is_infrastructure_failure_not_evaluator_decision() -> None:
    with TestClient(create_app()) as client:
        experiment, _, _ = _ready_experiment(client)
        run = client.post(f"/api/v1/experiments/{experiment['id']}/runs").json()
        assert client.post(f"/api/v1/experiment-runs/{run['id']}/execute").status_code == 202

    async def corrupt_and_execute() -> tuple[ProcessOutcome, int, ExperimentRunRecord]:
        async with SessionLocal() as session:
            condition = await session.get(
                ExperimentEvaluationConditionRecord, (UUID(str(experiment["id"])), 0)
            )
            assert condition is not None
            condition.evaluator_kind = "unsupported"
            await session.commit()
        outcome = await process_experiment_message(UUID(str(run["id"])))
        async with SessionLocal() as session:
            record = await session.get(ExperimentRunRecord, UUID(str(run["id"])))
            assert record is not None
            result_count = len(
                list(
                    await session.scalars(
                        select(ExperimentRunResultRecord).where(
                            ExperimentRunResultRecord.run_id == record.id
                        )
                    )
                )
            )
            return outcome, result_count, record

    outcome, result_count, record = asyncio.run(corrupt_and_execute())
    assert outcome is ProcessOutcome.FAILED
    assert record.status == "failed"
    assert record.error_category == "invalid_snapshot"
    assert result_count == 0


def test_recovery_is_concurrency_safe_and_enqueue_failure_remains_recoverable() -> None:
    with TestClient(create_app()) as client:
        experiment, _, _ = _ready_experiment(client)
        run = client.post(f"/api/v1/experiments/{experiment['id']}/runs").json()
        assert client.post(f"/api/v1/experiment-runs/{run['id']}/execute").status_code == 202
    run_id = UUID(str(run["id"]))
    recovery_time = datetime.now(UTC) + timedelta(minutes=10)

    async def scan() -> list[UUID]:
        async with SessionLocal() as session:
            return await recover_experiment_runs(session, now=recovery_time)

    async def concurrent_scan() -> list[list[UUID]]:
        return await asyncio.gather(scan(), scan())

    recovered = asyncio.run(concurrent_scan())
    assert sum(candidate == run_id for batch in recovered for candidate in batch) == 1

    def unavailable(_: UUID) -> None:
        raise RuntimeError("broker unavailable")

    # Make the reservation eligible again, then prove a failed broker send releases it.
    async def release_for_test() -> None:
        async with SessionLocal() as session, session.begin():
            record = await session.get(ExperimentRunRecord, run_id)
            assert record is not None
            record.last_enqueued_at = None

    asyncio.run(release_for_test())
    assert (
        asyncio.run(recover_experiments_once(enqueue=unavailable, now=recovery_time))
        == 0
    )

    async def verify_released() -> datetime | None:
        async with SessionLocal() as session:
            record = await session.get(ExperimentRunRecord, run_id)
            assert record is not None
            return record.last_enqueued_at

    assert asyncio.run(verify_released()) is None


def test_completed_experiment_supports_a_new_explicit_historical_run() -> None:
    with TestClient(create_app()) as client:
        experiment, _, _ = _ready_experiment(client)
        first = client.post(f"/api/v1/experiments/{experiment['id']}/runs").json()
        assert client.post(f"/api/v1/experiment-runs/{first['id']}/execute").status_code == 202
    assert (
        asyncio.run(process_experiment_message(UUID(str(first["id"]))))
        is ProcessOutcome.COMPLETED
    )

    with TestClient(create_app()) as client:
        second = client.post(f"/api/v1/experiments/{experiment['id']}/runs")
        listing = client.get(f"/api/v1/experiments/{experiment['id']}/runs")

    assert second.status_code == 201
    assert second.json()["id"] != first["id"]
    assert len(listing.json()["items"]) == 2
