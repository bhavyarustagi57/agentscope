from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from test_trace_ingestion import envelope, sdk_trace_payload

from agentscope_api.database import SessionLocal, engine
from agentscope_api.main import create_app
from agentscope_api.models.evaluation import (
    EvaluationDefinitionRecord,
    EvaluationResultRecord,
    EvaluationRunRecord,
)
from agentscope_api.services.evaluation_execution import (
    EvaluationRunExecutionFailed,
    ExistingEvaluationResults,
    InvalidEvaluationRunState,
    TraceNotFound,
    execute_evaluation_run,
    transition_run,
)

pytestmark = pytest.mark.usefixtures("clean_database")


def _trace(trace_id: str, output: Any) -> dict[str, Any]:
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


def _definition_and_run(
    client: TestClient,
    *,
    kind: str = "exact_match",
    config: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    definition = client.post(
        "/api/v1/evaluation-definitions",
        json={
            "name": "execution contract",
            "evaluator_kind": kind,
            "evaluator_config": config or {"expected": "Paris", "case_sensitive": False},
        },
    ).json()
    run = client.post("/api/v1/evaluation-runs", json={"definition_id": definition["id"]}).json()
    return definition, run


def test_execution_uses_run_snapshot_batch_loads_traces_and_persists_mixed_results() -> None:
    traces = [
        _trace("tr_eval_pass", "PARIS"),
        _trace("tr_eval_fail", "London"),
        _trace("tr_eval_missing", None),
    ]
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(*traces)).status_code == 202
        definition, run = _definition_and_run(client)

    statements: list[str] = []

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    async def execute_and_verify() -> None:
        async with SessionLocal() as session:
            definition_record = await session.get(
                EvaluationDefinitionRecord, UUID(str(definition["id"]))
            )
            assert definition_record is not None
            definition_record.evaluator_config = {
                "expected": "London",
                "case_sensitive": True,
            }
            await session.commit()

        event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
        try:
            async with SessionLocal() as session:
                summary = await execute_evaluation_run(
                    session,
                    UUID(str(run["id"])),
                    [trace["trace_id"] for trace in traces],
                )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", record_statement)

        assert summary.status == "completed"
        assert summary.result_count == 3
        assert summary.error_count == 1

        async with SessionLocal() as session:
            stored_run = await session.get(EvaluationRunRecord, UUID(str(run["id"])))
            results = (
                await session.scalars(
                    select(EvaluationResultRecord).where(
                        EvaluationResultRecord.run_id == UUID(str(run["id"]))
                    )
                )
            ).all()
        assert stored_run is not None
        assert stored_run.status == "completed"
        assert stored_run.started_at is not None
        assert stored_run.completed_at is not None
        assert stored_run.completed_at >= stored_run.started_at
        by_trace = {result.trace_id: result for result in results}
        assert (by_trace["tr_eval_pass"].outcome, by_trace["tr_eval_pass"].score) == (
            "passed",
            1.0,
        )
        assert (by_trace["tr_eval_fail"].outcome, by_trace["tr_eval_fail"].score) == (
            "failed",
            0.0,
        )
        assert (by_trace["tr_eval_missing"].outcome, by_trace["tr_eval_missing"].score) == (
            "error",
            None,
        )
        assert by_trace["tr_eval_missing"].details["reason_code"] == "candidate_not_captured"
        assert all("candidate" not in result.details for result in results)

    asyncio.run(execute_and_verify())
    with TestClient(create_app()) as client:
        read = client.get(f"/api/v1/evaluation-runs/{run['id']}")
    assert read.status_code == 200
    assert read.json()["subject_count"] == read.json()["result_count"] == 3
    trace_reads = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT") and "FROM traces" in statement
    ]
    assert len(trace_reads) == 1
    assert not any("FROM spans" in statement for statement in statements)


def test_missing_trace_rejects_execution_without_changing_pending_run() -> None:
    with TestClient(create_app()) as client:
        _, run = _definition_and_run(client)

    async def verify() -> None:
        async with SessionLocal() as session:
            with pytest.raises(TraceNotFound, match="tr_missing"):
                await execute_evaluation_run(session, UUID(str(run["id"])), ["tr_missing"])
        async with SessionLocal() as session:
            record = await session.get(EvaluationRunRecord, UUID(str(run["id"])))
            result_count = await session.scalar(
                select(func.count())
                .select_from(EvaluationResultRecord)
                .where(EvaluationResultRecord.run_id == UUID(str(run["id"])))
            )
        assert record is not None
        assert record.status == "pending"
        assert record.started_at is record.completed_at is None
        assert result_count == 0

    asyncio.run(verify())


def test_invalid_snapshot_fails_run_without_subject_results() -> None:
    trace = _trace("tr_invalid_snapshot", "Paris")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        _, run = _definition_and_run(client)

    async def verify() -> None:
        async with SessionLocal() as session:
            record = await session.get(EvaluationRunRecord, UUID(str(run["id"])))
            assert record is not None
            record.evaluator_config = {}
            await session.commit()
        async with SessionLocal() as session:
            with pytest.raises(EvaluationRunExecutionFailed):
                await execute_evaluation_run(session, UUID(str(run["id"])), [trace["trace_id"]])
        async with SessionLocal() as session:
            record = await session.get(EvaluationRunRecord, UUID(str(run["id"])))
            result_count = await session.scalar(
                select(func.count())
                .select_from(EvaluationResultRecord)
                .where(EvaluationResultRecord.run_id == UUID(str(run["id"])))
            )
        assert record is not None
        assert record.status == "failed"
        assert record.started_at is not None
        assert record.completed_at is not None
        assert record.error_message == "invalid evaluator snapshot"
        assert result_count == 0

    asyncio.run(verify())


def test_queued_run_executes_once_and_invalid_transitions_are_rejected() -> None:
    trace = _trace("tr_single_execution", "Paris")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        _, run = _definition_and_run(client)

    async def verify() -> None:
        run_id = UUID(str(run["id"]))
        async with SessionLocal() as session, session.begin():
            record = await session.get(EvaluationRunRecord, run_id)
            assert record is not None
            transition_run(record, "queued")
        async with SessionLocal() as session:
            await execute_evaluation_run(session, run_id, [trace["trace_id"]])
        async with SessionLocal() as session:
            with pytest.raises(InvalidEvaluationRunState):
                await execute_evaluation_run(session, run_id, [trace["trace_id"]])
        async with SessionLocal() as session, session.begin():
            record = await session.get(EvaluationRunRecord, run_id)
            assert record is not None
            with pytest.raises(InvalidEvaluationRunState):
                transition_run(record, "running")
        async with SessionLocal() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvaluationResultRecord)
                    .where(EvaluationResultRecord.run_id == run_id)
                )
                == 1
            )

    asyncio.run(verify())


def test_existing_results_on_an_executable_run_are_rejected_not_overwritten() -> None:
    trace = _trace("tr_existing_result", "Paris")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        _, run = _definition_and_run(client)

    async def verify() -> None:
        run_id = UUID(str(run["id"]))
        async with SessionLocal() as session, session.begin():
            session.add(
                EvaluationResultRecord(
                    run_id=run_id,
                    trace_id=trace["trace_id"],
                    outcome="failed",
                    score=0.0,
                    details={"reason_code": "preexisting"},
                )
            )
        async with SessionLocal() as session:
            with pytest.raises(ExistingEvaluationResults):
                await execute_evaluation_run(session, run_id, [trace["trace_id"]])
        async with SessionLocal() as session:
            record = await session.get(EvaluationRunRecord, run_id)
            results = (
                await session.scalars(
                    select(EvaluationResultRecord).where(EvaluationResultRecord.run_id == run_id)
                )
            ).all()
        assert record is not None and record.status == "pending"
        assert len(results) == 1 and results[0].details["reason_code"] == "preexisting"

    asyncio.run(verify())


def test_concurrent_execution_allows_exactly_one_completion() -> None:
    trace = _trace("tr_concurrent_execution", "Paris")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(trace)).status_code == 202
        _, run = _definition_and_run(client)

    async def verify() -> None:
        run_id = UUID(str(run["id"]))

        async def execute_once() -> object:
            async with SessionLocal() as session:
                try:
                    return await execute_evaluation_run(session, run_id, [trace["trace_id"]])
                except InvalidEvaluationRunState as error:
                    return error

        outcomes = await asyncio.gather(execute_once(), execute_once())
        assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
        assert sum(isinstance(outcome, InvalidEvaluationRunState) for outcome in outcomes) == 1

        async with SessionLocal() as session:
            result_count = await session.scalar(
                select(func.count())
                .select_from(EvaluationResultRecord)
                .where(EvaluationResultRecord.run_id == run_id)
            )
        assert result_count == 1

    asyncio.run(verify())
