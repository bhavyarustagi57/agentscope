from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from dramatiq import Actor
from dramatiq.middleware import AsyncIO
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from test_trace_ingestion import envelope, sdk_trace_payload

from agentscope_api.database import SessionLocal
from agentscope_api.jobs.broker import broker
from agentscope_api.jobs.evaluations import evaluation_run_job
from agentscope_api.jobs.recover import (
    EvaluationRecoveryMiddleware,
    recover_once,
    run_recovery_loop,
)
from agentscope_api.main import create_app
from agentscope_api.models.evaluation import (
    EvaluationDefinitionRecord,
    EvaluationResultRecord,
    EvaluationRunRecord,
    EvaluationRunSubjectRecord,
)
from agentscope_api.services.evaluation_execution import transition_run
from agentscope_api.services.evaluation_orchestration import (
    ClaimOutcome,
    ProcessOutcome,
    claim_evaluation_run,
    finalize_evaluation_run,
    mark_run_enqueued,
    process_evaluation_message,
    recover_evaluation_runs,
)

pytestmark = pytest.mark.usefixtures("clean_database")


def test_worker_actor_has_bounded_retry_policy() -> None:
    assert isinstance(evaluation_run_job, Actor)
    assert evaluation_run_job.queue_name == "evaluations"
    assert evaluation_run_job.options == {
        "max_retries": 3,
        "min_backoff": 5_000,
        "max_backoff": 60_000,
    }


def test_worker_registers_periodic_recovery_fork() -> None:
    assert any(isinstance(item, AsyncIO) for item in broker.middleware)
    middleware = next(
        item for item in broker.middleware if isinstance(item, EvaluationRecoveryMiddleware)
    )
    assert middleware.forks == [run_recovery_loop]


def test_periodic_recovery_loop_runs_immediately_then_at_the_configured_interval() -> None:
    class StopLoop(Exception):
        pass

    tick = AsyncMock(return_value=0)
    with (
        patch("agentscope_api.jobs.recover.recover_once", tick),
        patch("agentscope_api.jobs.recover.time.sleep", side_effect=[None, StopLoop]) as sleep,
        pytest.raises(StopLoop),
    ):
        run_recovery_loop()

    assert tick.await_count == 2
    assert sleep.call_args_list[0].args == (60,)


def _trace(trace_id: str, output: Any = "Paris") -> dict[str, Any]:
    payload = deepcopy(sdk_trace_payload(trace_id))
    payload["trace_id"] = trace_id
    payload["output"] = output
    span_ids = {
        span["span_id"]: f"sp_{trace_id}_{index}" for index, span in enumerate(payload["spans"])
    }
    for span in payload["spans"]:
        span["trace_id"] = trace_id
        span["span_id"] = span_ids[span["span_id"]]
        span["parent_span_id"] = span_ids.get(span["parent_span_id"])
    return payload


def _run(client: TestClient) -> dict[str, object]:
    definition = client.post(
        "/api/v1/evaluation-definitions",
        json={
            "name": "async exact match",
            "evaluator_kind": "exact_match",
            "evaluator_config": {"expected": "Paris", "case_sensitive": True},
        },
    ).json()
    return client.post("/api/v1/evaluation-runs", json={"definition_id": definition["id"]}).json()


def test_submission_commits_ordered_subjects_before_enqueue() -> None:
    traces = [_trace("tr_async_1"), _trace("tr_async_2", "London")]
    observed: list[tuple[UUID, str, list[str]]] = []

    def enqueue(run_id: UUID) -> None:
        async def inspect() -> None:
            async with SessionLocal() as session:
                run = await session.get(EvaluationRunRecord, run_id)
                subjects = (
                    await session.scalars(
                        select(EvaluationRunSubjectRecord)
                        .where(EvaluationRunSubjectRecord.run_id == run_id)
                        .order_by(EvaluationRunSubjectRecord.position)
                    )
                ).all()
            assert run is not None
            observed.append((run_id, run.status, [subject.trace_id for subject in subjects]))

        asyncio.run(inspect())

    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(*traces)).status_code == 202
        run = _run(client)
        with patch(
            "agentscope_api.api.routes.evaluations.enqueue_evaluation_run", side_effect=enqueue
        ):
            response = client.post(
                f"/api/v1/evaluation-runs/{run['id']}/execute",
                json={"trace_ids": [trace["trace_id"] for trace in traces]},
            )

    assert response.status_code == 202
    assert response.json() == {
        "run_id": run["id"],
        "status": "queued",
        "subject_count": 2,
        "queue_delivery": "enqueued",
    }
    assert observed == [(UUID(str(run["id"])), "queued", ["tr_async_1", "tr_async_2"])]


def test_enqueue_failure_preserves_recoverable_intent() -> None:
    trace = _trace("tr_deferred")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        run = _run(client)
        with patch(
            "agentscope_api.api.routes.evaluations.enqueue_evaluation_run",
            side_effect=ConnectionError("redis unavailable"),
        ):
            response = client.post(
                f"/api/v1/evaluation-runs/{run['id']}/execute",
                json={"trace_ids": [trace["trace_id"]]},
            )

    assert response.status_code == 202
    assert response.json()["queue_delivery"] == "deferred"

    async def verify() -> None:
        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, UUID(str(run["id"])))
            subjects = (
                await session.scalars(
                    select(EvaluationRunSubjectRecord).where(
                        EvaluationRunSubjectRecord.run_id == UUID(str(run["id"]))
                    )
                )
            ).all()
        assert stored is not None
        assert stored.status == "queued"
        assert stored.queued_at is not None
        assert stored.last_enqueued_at is None
        assert [subject.trace_id for subject in subjects] == [trace["trace_id"]]

    asyncio.run(verify())


def test_submission_is_idempotent_only_for_the_same_ordered_subject_list() -> None:
    traces = [_trace("tr_same_1"), _trace("tr_same_2")]
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(*traces)).status_code == 202
        run = _run(client)
        with patch("agentscope_api.api.routes.evaluations.enqueue_evaluation_run"):
            first = client.post(
                f"/api/v1/evaluation-runs/{run['id']}/execute",
                json={"trace_ids": ["tr_same_1", "tr_same_2"]},
            )
            identical = client.post(
                f"/api/v1/evaluation-runs/{run['id']}/execute",
                json={"trace_ids": ["tr_same_1", "tr_same_2"]},
            )
            reordered = client.post(
                f"/api/v1/evaluation-runs/{run['id']}/execute",
                json={"trace_ids": ["tr_same_2", "tr_same_1"]},
            )
            changed = client.post(
                f"/api/v1/evaluation-runs/{run['id']}/execute",
                json={"trace_ids": ["tr_same_1"]},
            )

    assert first.status_code == identical.status_code == 202
    for conflict in (reordered, changed):
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "EVALUATION_EXECUTION_CONFLICT"


def test_submission_rejects_preexisting_results() -> None:
    trace = _trace("tr_preexisting")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        run = _run(client)

    async def seed_result() -> None:
        async with SessionLocal() as session, session.begin():
            session.add(
                EvaluationResultRecord(
                    run_id=UUID(str(run["id"])),
                    trace_id=trace["trace_id"],
                    outcome="failed",
                    score=0.0,
                    details={"reason_code": "preexisting"},
                )
            )

    asyncio.run(seed_result())
    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/v1/evaluation-runs/{run['id']}/execute",
            json={"trace_ids": [trace["trace_id"]]},
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EVALUATION_EXECUTION_CONFLICT"


@pytest.mark.parametrize(
    ("path_id", "trace_ids", "status", "code"),
    [
        ("00000000-0000-0000-0000-000000000000", ["tr_valid"], 404, "EVALUATION_RUN_NOT_FOUND"),
        (None, ["tr_missing"], 404, "TRACE_NOT_FOUND"),
        (None, [], 422, "VALIDATION_ERROR"),
        (None, ["duplicate", "duplicate"], 422, "VALIDATION_ERROR"),
        (None, [f"tr_{index}" for index in range(1_001)], 422, "VALIDATION_ERROR"),
    ],
)
def test_submission_rejects_invalid_run_or_subjects(
    path_id: str | None, trace_ids: list[str], status: int, code: str
) -> None:
    with TestClient(create_app()) as client:
        trace = _trace("tr_valid")
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        run = _run(client)
        response = client.post(
            f"/api/v1/evaluation-runs/{path_id or run['id']}/execute",
            json={"trace_ids": trace_ids},
        )

    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def _submit_without_queue(client: TestClient, run: dict[str, object], trace_ids: list[str]) -> None:
    with patch("agentscope_api.api.routes.evaluations.enqueue_evaluation_run"):
        response = client.post(
            f"/api/v1/evaluation-runs/{run['id']}/execute", json={"trace_ids": trace_ids}
        )
    assert response.status_code == 202, response.text


def test_worker_executes_snapshot_and_duplicate_delivery_is_harmless() -> None:
    traces = [
        _trace("tr_worker_pass", "Paris"),
        _trace("tr_worker_fail", "London"),
        _trace("tr_worker_error", None),
    ]
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(*traces)).status_code == 202
        run = _run(client)
        _submit_without_queue(client, run, [trace["trace_id"] for trace in traces])

    async def verify() -> None:
        run_id = UUID(str(run["id"]))
        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, run_id)
            assert stored is not None
            definition = await session.get(EvaluationDefinitionRecord, stored.definition_id)
            assert definition is not None
            definition.evaluator_config = {"expected": "London", "case_sensitive": True}
            await session.commit()

        assert await process_evaluation_message(run_id) is ProcessOutcome.COMPLETED
        assert await process_evaluation_message(run_id) is ProcessOutcome.IGNORED

        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, run_id)
            results = (
                await session.scalars(
                    select(EvaluationResultRecord).where(EvaluationResultRecord.run_id == run_id)
                )
            ).all()
        assert stored is not None and stored.status == "completed"
        assert stored.attempt_count == 1
        by_trace = {result.trace_id: result for result in results}
        assert by_trace["tr_worker_pass"].outcome == "passed"
        assert by_trace["tr_worker_fail"].outcome == "failed"
        assert by_trace["tr_worker_error"].outcome == "error"
        assert len(results) == 3

    asyncio.run(verify())
    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/v1/evaluation-runs/{run['id']}/execute",
            json={"trace_ids": [trace["trace_id"] for trace in traces]},
        )
    assert response.status_code == 409


def test_two_concurrent_claims_have_one_database_winner() -> None:
    trace = _trace("tr_claim")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        run = _run(client)
        _submit_without_queue(client, run, [trace["trace_id"]])

    async def verify() -> None:
        run_id = UUID(str(run["id"]))

        async def claim_once() -> object:
            async with SessionLocal() as session:
                return await claim_evaluation_run(session, run_id)

        claims = await asyncio.gather(claim_once(), claim_once())
        assert sum(claim.outcome is ClaimOutcome.CLAIMED for claim in claims) == 1
        assert sum(claim.outcome is ClaimOutcome.IGNORED for claim in claims) == 1
        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, run_id)
        assert stored is not None and stored.status == "running"
        assert stored.attempt_count == 1

    asyncio.run(verify())
    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/v1/evaluation-runs/{run['id']}/execute",
            json={"trace_ids": [trace["trace_id"]]},
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EVALUATION_RUN_NOT_EXECUTABLE"


def test_invalid_snapshot_is_a_permanent_failed_run() -> None:
    trace = _trace("tr_bad_snapshot")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        run = _run(client)
        _submit_without_queue(client, run, [trace["trace_id"]])

    async def verify() -> None:
        run_id = UUID(str(run["id"]))
        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, run_id)
            assert stored is not None
            stored.evaluator_config = {}
            await session.commit()
        assert await process_evaluation_message(run_id) is ProcessOutcome.FAILED
        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, run_id)
            count = await session.scalar(
                select(func.count())
                .select_from(EvaluationResultRecord)
                .where(EvaluationResultRecord.run_id == run_id)
            )
        assert stored is not None and stored.status == "failed"
        assert stored.error_message == "invalid evaluator snapshot"
        assert count == 0
        assert await process_evaluation_message(run_id) is ProcessOutcome.IGNORED

    asyncio.run(verify())
    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/v1/evaluation-runs/{run['id']}/execute",
            json={"trace_ids": [trace["trace_id"]]},
        )
    assert response.status_code == 409


def test_pending_run_message_is_harmless() -> None:
    with TestClient(create_app()) as client:
        run = _run(client)

    async def verify() -> None:
        run_id = UUID(str(run["id"]))
        assert await process_evaluation_message(run_id) is ProcessOutcome.IGNORED
        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, run_id)
        assert stored is not None and stored.status == "pending"

    asyncio.run(verify())


def test_transient_finalization_failure_requeues_without_partial_results() -> None:
    trace = _trace("tr_retry")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        run = _run(client)
        _submit_without_queue(client, run, [trace["trace_id"]])

    async def verify() -> None:
        run_id = UUID(str(run["id"]))
        with patch(
            "agentscope_api.services.evaluation_orchestration.finalize_evaluation_run",
            side_effect=SQLAlchemyError("temporary database failure"),
        ):
            with pytest.raises(SQLAlchemyError):
                await process_evaluation_message(run_id)
        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, run_id)
            count = await session.scalar(
                select(func.count())
                .select_from(EvaluationResultRecord)
                .where(EvaluationResultRecord.run_id == run_id)
            )
        assert stored is not None and stored.status == "queued"
        assert stored.started_at is None
        assert count == 0

        assert await process_evaluation_message(run_id) is ProcessOutcome.COMPLETED
        async with SessionLocal() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(EvaluationResultRecord)
                .where(EvaluationResultRecord.run_id == run_id)
            )
        assert count == 1

    asyncio.run(verify())


def test_queued_recovery_reserves_a_bounded_batch_and_exhaustion_fails() -> None:
    trace = _trace("tr_recovery_batch")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        runs = [_run(client), _run(client)]
        for run in runs:
            _submit_without_queue(client, run, [trace["trace_id"]])

    async def verify() -> None:
        async with SessionLocal() as session:
            stored_runs = (
                await session.scalars(
                    select(EvaluationRunRecord).where(
                        EvaluationRunRecord.id.in_([UUID(str(run["id"])) for run in runs])
                    )
                )
            ).all()
            for stored in stored_runs:
                stored.last_enqueued_at = None
            await session.commit()
        async with SessionLocal() as session:
            first = await recover_evaluation_runs(session, limit=1)
        async with SessionLocal() as session:
            await mark_run_enqueued(session, first[0])
        async with SessionLocal() as session:
            second = await recover_evaluation_runs(session, limit=1)
        async with SessionLocal() as session:
            await mark_run_enqueued(session, second[0])
        assert len(first) == len(second) == 1
        assert set(first + second) == {UUID(str(run["id"])) for run in runs}
        async with SessionLocal() as session:
            assert await recover_evaluation_runs(session, limit=100) == []

        exhausted_id = UUID(str(runs[0]["id"]))
        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, exhausted_id)
            assert stored is not None
            stored.attempt_count = 4
            stored.last_enqueued_at = None
            await session.commit()
        async with SessionLocal() as session:
            claim = await claim_evaluation_run(session, exhausted_id)
        assert claim.outcome is ClaimOutcome.EXHAUSTED
        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, exhausted_id)
        assert stored is not None and stored.status == "failed"

    asyncio.run(verify())


def test_stale_recovery_is_bounded_and_claim_generation_rejects_old_finalizer() -> None:
    trace = _trace("tr_stale")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        run = _run(client)
        _submit_without_queue(client, run, [trace["trace_id"]])

    async def verify() -> None:
        run_id = UUID(str(run["id"]))
        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, run_id)
        assert stored is not None
        claimed_at = stored.created_at + timedelta(seconds=1)
        async with SessionLocal() as session:
            first_claim = await claim_evaluation_run(session, run_id, now=claimed_at)
        assert first_claim.outcome is ClaimOutcome.CLAIMED
        assert first_claim.attempt == 1

        async with SessionLocal() as session:
            recovered = await recover_evaluation_runs(
                session, now=claimed_at + timedelta(minutes=10), limit=1
            )
        assert recovered == [run_id]
        async with SessionLocal() as session:
            await mark_run_enqueued(session, run_id, now=claimed_at + timedelta(minutes=10))

        async with SessionLocal() as session:
            second_claim = await claim_evaluation_run(session, run_id)
        assert second_claim.outcome is ClaimOutcome.CLAIMED
        assert second_claim.attempt == 2

        assert await finalize_evaluation_run(run_id, first_claim.attempt, []) is False
        async with SessionLocal() as session:
            duplicate_recovery = await recover_evaluation_runs(session, limit=1)
        assert duplicate_recovery == []

    asyncio.run(verify())


def test_current_attempt_cannot_finalize_an_incomplete_subject_set() -> None:
    trace = _trace("tr_incomplete_finalization")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        run = _run(client)
        _submit_without_queue(client, run, [trace["trace_id"]])

    async def verify() -> None:
        run_id = UUID(str(run["id"]))
        async with SessionLocal() as session:
            claim = await claim_evaluation_run(session, run_id)
        assert claim.outcome is ClaimOutcome.CLAIMED
        assert await finalize_evaluation_run(run_id, claim.attempt, []) is False
        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, run_id)
            count = await session.scalar(
                select(func.count())
                .select_from(EvaluationResultRecord)
                .where(EvaluationResultRecord.run_id == run_id)
            )
        assert stored is not None and stored.status == "running"
        assert count == 0

    asyncio.run(verify())


def test_concurrent_recovery_ticks_do_not_steal_fresh_or_terminal_work() -> None:
    trace = _trace("tr_recovery_states")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        queued, fresh, terminal, exhausted = [_run(client) for _ in range(4)]
        for run in (queued, fresh, terminal, exhausted):
            _submit_without_queue(client, run, [trace["trace_id"]])

    async def verify() -> None:
        now = datetime.now(UTC) + timedelta(minutes=1)
        ids = [UUID(str(run["id"])) for run in (queued, fresh, terminal, exhausted)]
        async with SessionLocal() as session:
            records = {
                record.id: record
                for record in await session.scalars(
                    select(EvaluationRunRecord).where(EvaluationRunRecord.id.in_(ids))
                )
            }
            records[ids[0]].last_enqueued_at = None
            records[ids[1]].status = "running"
            records[ids[1]].started_at = now
            records[ids[1]].last_enqueued_at = now
            records[ids[2]].status = "running"
            records[ids[2]].started_at = now - timedelta(minutes=1)
            transition_run(records[ids[2]], "completed", now=now)
            records[ids[3]].attempt_count = 4
            records[ids[3]].last_enqueued_at = None
            await session.commit()

        async def scan() -> list[UUID]:
            async with SessionLocal() as session:
                return await recover_evaluation_runs(session, now=now, limit=100)

        scans = await asyncio.gather(scan(), scan())
        recovered = [item for scan_result in scans for item in scan_result]
        if not recovered:
            recovered = await scan()
        assert recovered == [ids[0]]

        async with SessionLocal() as session:
            records = {
                record.id: record
                for record in await session.scalars(
                    select(EvaluationRunRecord).where(EvaluationRunRecord.id.in_(ids))
                )
            }
        assert records[ids[0]].status == "queued"
        assert records[ids[1]].status == "running"
        assert records[ids[1]].attempt_count == 0
        assert records[ids[2]].status == "completed"
        assert records[ids[3]].status == "failed"

    asyncio.run(verify())


def test_recovery_reservation_prevents_duplicate_ticks_and_enqueue_failure_releases_it() -> None:
    trace = _trace("tr_periodic_recovery")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        run = _run(client)
        _submit_without_queue(client, run, [trace["trace_id"]])

    async def verify() -> None:
        run_id = UUID(str(run["id"]))
        snapshot_before: tuple[str, dict[str, object]]
        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, run_id)
            assert stored is not None
            assert stored.queued_at is not None
            reserved_at = stored.queued_at + timedelta(minutes=10)
            stored.last_enqueued_at = None
            snapshot_before = (stored.evaluator_kind, deepcopy(stored.evaluator_config))
            await session.commit()

        async with SessionLocal() as session:
            first = await recover_evaluation_runs(session, now=reserved_at, limit=1)
        async with SessionLocal() as session:
            duplicate = await recover_evaluation_runs(session, now=reserved_at, limit=1)
            stored = await session.get(EvaluationRunRecord, run_id)
        assert first == [run_id]
        assert duplicate == []
        assert stored is not None and stored.last_enqueued_at == reserved_at

        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, run_id)
            assert stored is not None
            stored.last_enqueued_at = None
            await session.commit()

        def unavailable(_: UUID) -> None:
            raise ConnectionError("redis unavailable")

        assert await recover_once(enqueue=unavailable, now=reserved_at) == 0
        sent: list[UUID] = []
        assert await recover_once(enqueue=sent.append, now=reserved_at) == 1
        assert sent == [run_id]

        async with SessionLocal() as session:
            stored = await session.get(EvaluationRunRecord, run_id)
            subjects = (
                await session.scalars(
                    select(EvaluationRunSubjectRecord)
                    .where(EvaluationRunSubjectRecord.run_id == run_id)
                    .order_by(EvaluationRunSubjectRecord.position)
                )
            ).all()
        assert stored is not None
        assert (stored.evaluator_kind, stored.evaluator_config) == snapshot_before
        assert [subject.trace_id for subject in subjects] == [trace["trace_id"]]

    asyncio.run(verify())
