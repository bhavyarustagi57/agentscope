from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

import pytest
from sqlalchemy import select

from agentscope_api.database import SessionLocal
from agentscope_api.jobs.recover import recover_monitoring_once
from agentscope_api.models.evaluation import (
    EvaluationDefinitionRecord,
    EvaluationResultRecord,
    EvaluationRunRecord,
)
from agentscope_api.models.monitoring import MonitoringDefinitionRecord, MonitoringSnapshotRecord
from agentscope_api.models.trace import SpanRecord, TraceRecord
from agentscope_api.services.monitoring_orchestration import (
    MaterializationOutcome,
    claim_snapshot,
    discover_missing_snapshots,
    finalize_snapshot,
    process_monitoring_message,
    recover_snapshots,
)

pytestmark = pytest.mark.usefixtures("clean_database")

START = datetime(2026, 9, 25, 10, tzinfo=UTC)
END = datetime(2026, 9, 25, 11, tzinfo=UTC)


def _trace(
    trace_id: str,
    started_at: datetime,
    status: str,
    duration_ms: float | None,
    tokens: tuple[int, int, int] | None = None,
) -> tuple[TraceRecord, SpanRecord | None]:
    trace = TraceRecord(
        trace_id=trace_id,
        name="checkout",
        status=status,
        started_at=started_at,
        ended_at=started_at + timedelta(milliseconds=duration_ms)
        if duration_ms is not None
        else None,
        duration_ms=duration_ms,
        input=None,
        output=None,
        error={"type": "Failure", "message": "failed"} if status == "error" else None,
        metadata_={},
        tags=[],
        payload_fingerprint=(trace_id.encode().hex() + "0" * 64)[:64],
    )
    if tokens is None:
        return trace, None
    input_tokens, output_tokens, total_tokens = tokens
    return trace, SpanRecord(
        span_id=f"span-{trace_id}",
        trace_id=trace_id,
        parent_span_id=None,
        name="model",
        kind="llm",
        status=status,
        started_at=started_at,
        ended_at=started_at,
        duration_ms=0,
        input=None,
        output=None,
        error=None,
        metadata_={},
        attributes={},
        events=[],
        llm={
            "token_usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            }
        },
    )


async def _seed(*, with_evaluations: bool = True) -> UUID:
    async with SessionLocal() as session, session.begin():
        evaluation = EvaluationDefinitionRecord(
            name="quality",
            description=None,
            evaluator_kind="exact_match",
            evaluator_config={"expected": True, "case_sensitive": True},
            is_enabled=True,
        )
        session.add(evaluation)
        await session.flush()
        monitor = MonitoringDefinitionRecord(
            name="hourly checkout",
            description=None,
            is_enabled=True,
            window_duration_seconds=3_600,
            trace_name="checkout",
            trace_status=None,
            evaluation_definition_id=evaluation.id if with_evaluations else None,
        )
        session.add(monitor)
        await session.flush()
        snapshot = MonitoringSnapshotRecord(
            monitoring_definition_id=monitor.id,
            window_start=START,
            window_end=END,
            status="queued",
            queued_at=START,
        )
        session.add(snapshot)
        await session.flush()
        return snapshot.id


def test_materialization_uses_half_open_boundaries_and_persists_canonical_metrics() -> None:
    async def exercise() -> UUID:
        snapshot_id = await _seed()
        traces = [
            _trace("at-start", START, "success", 100, (4, 6, 10)),
            _trace("inside", START + timedelta(minutes=20), "success", 300, (8, 12, 20)),
            _trace("missing", START + timedelta(minutes=30), "error", None),
            _trace("at-end", END, "success", 9_999, (50, 50, 100)),
        ]
        async with SessionLocal() as session, session.begin():
            for trace, _span in traces:
                session.add(trace)
            await session.flush()
            for _trace_record, span in traces:
                if span is not None:
                    session.add(span)
            snapshot = await session.get(MonitoringSnapshotRecord, snapshot_id)
            assert snapshot is not None
            monitor = await session.get(
                MonitoringDefinitionRecord, snapshot.monitoring_definition_id
            )
            assert monitor is not None and monitor.evaluation_definition_id is not None
            run = EvaluationRunRecord(
                definition_id=monitor.evaluation_definition_id,
                definition_name="quality",
                evaluator_kind="exact_match",
                evaluator_config={"expected": True, "case_sensitive": True},
                status="completed",
                created_at=START,
                started_at=START,
                completed_at=START + timedelta(minutes=1),
            )
            session.add(run)
            await session.flush()
            for index, (outcome, created_at) in enumerate(
                [
                    ("passed", START),
                    ("failed", START + timedelta(minutes=1)),
                    ("error", START + timedelta(minutes=2)),
                    ("passed", END),
                ]
            ):
                session.add(
                    EvaluationResultRecord(
                        run_id=run.id,
                        trace_id=traces[index][0].trace_id,
                        outcome=outcome,
                        score=None,
                        details={},
                        created_at=created_at,
                    )
                )
        assert await process_monitoring_message(snapshot_id) is MaterializationOutcome.COMPLETED
        return snapshot_id

    snapshot_id = asyncio.run(exercise())

    async def verify() -> MonitoringSnapshotRecord:
        async with SessionLocal() as session:
            record = await session.get(MonitoringSnapshotRecord, snapshot_id)
            assert record is not None
            return record

    record = asyncio.run(verify())
    assert record.status == "completed"
    assert (record.trace_count, record.successful_trace_count, record.failed_trace_count) == (
        3,
        2,
        1,
    )
    assert record.success_rate == pytest.approx(2 / 3)
    assert record.failure_rate == pytest.approx(1 / 3)
    assert record.duration_sample_count == 2
    assert record.mean_duration_ms == record.median_duration_ms == 200
    assert record.p95_duration_ms == 290
    assert (record.token_sample_count, record.input_tokens, record.output_tokens) == (2, 12, 18)
    assert (record.total_tokens, record.mean_total_tokens) == (30, 15)
    assert (
        record.evaluated_result_count,
        record.passed_evaluation_count,
        record.failed_evaluation_count,
        record.evaluator_error_count,
        record.valid_binary_evaluation_count,
    ) == (3, 1, 1, 1, 2)
    assert record.evaluation_pass_rate == 0.5
    assert record.evaluation_error_rate == pytest.approx(1 / 3)


def test_empty_window_completes_with_zero_populations_and_null_distributions() -> None:
    async def exercise() -> MonitoringSnapshotRecord:
        snapshot_id = await _seed(with_evaluations=False)
        assert await process_monitoring_message(snapshot_id) is MaterializationOutcome.COMPLETED
        async with SessionLocal() as session:
            record = await session.get(MonitoringSnapshotRecord, snapshot_id)
            assert record is not None
            return record

    record = asyncio.run(exercise())
    assert record.status == "completed"
    assert record.trace_count == record.duration_sample_count == record.token_sample_count == 0
    assert record.success_rate is record.mean_duration_ms is record.total_tokens is None
    assert record.evaluation_pass_rate is record.evaluation_error_rate is None


def test_trace_without_token_capture_preserves_null_token_metrics() -> None:
    async def exercise() -> MonitoringSnapshotRecord:
        snapshot_id = await _seed(with_evaluations=False)
        trace, _ = _trace("no-tokens", START + timedelta(minutes=1), "success", 10)
        async with SessionLocal() as session, session.begin():
            session.add(trace)
        assert await process_monitoring_message(snapshot_id) is MaterializationOutcome.COMPLETED
        async with SessionLocal() as session:
            record = await session.get(MonitoringSnapshotRecord, snapshot_id)
            assert record is not None
            return record

    record = asyncio.run(exercise())
    assert record.trace_count == 1
    assert record.token_sample_count == 0
    assert record.input_tokens is record.output_tokens is record.total_tokens is None
    assert record.mean_total_tokens is None


def test_duplicate_delivery_and_stale_owner_cannot_overwrite_new_claim() -> None:
    async def exercise() -> None:
        snapshot_id = await _seed(with_evaluations=False)
        async with SessionLocal() as session:
            first = await claim_snapshot(session, snapshot_id, now=START)
        assert first is not None and first.attempt == 1
        async with SessionLocal() as session:
            assert await claim_snapshot(session, snapshot_id, now=START) is None
        async with SessionLocal() as session:
            assert await recover_snapshots(session, now=START + timedelta(minutes=10)) == [
                snapshot_id
            ]
        async with SessionLocal() as session:
            second = await claim_snapshot(session, snapshot_id, now=START + timedelta(minutes=10))
        assert second is not None and second.attempt == 2
        assert not await finalize_snapshot(first, {}, now=START + timedelta(minutes=10))
        assert await finalize_snapshot(second, {}, now=START + timedelta(minutes=10))
        assert await process_monitoring_message(snapshot_id) is MaterializationOutcome.IGNORED

    asyncio.run(exercise())


def test_discovery_is_bounded_skips_disabled_and_never_schedules_open_window() -> None:
    now = datetime(2026, 9, 25, 12, 37, tzinfo=UTC)

    async def exercise() -> tuple[list[UUID], list[UUID], list[MonitoringSnapshotRecord]]:
        async with SessionLocal() as session, session.begin():
            session.add_all(
                [
                    MonitoringDefinitionRecord(
                        name="enabled",
                        description=None,
                        is_enabled=True,
                        window_duration_seconds=300,
                        trace_name=None,
                        trace_status=None,
                        evaluation_definition_id=None,
                    ),
                    MonitoringDefinitionRecord(
                        name="disabled",
                        description=None,
                        is_enabled=False,
                        window_duration_seconds=300,
                        trace_name=None,
                        trace_status=None,
                        evaluation_definition_id=None,
                    ),
                ]
            )
        async with SessionLocal() as session:
            first = await discover_missing_snapshots(session, now=now, max_windows=3)
        async with SessionLocal() as session:
            second = await discover_missing_snapshots(session, now=now, max_windows=3)
            rows = list(await session.scalars(select(MonitoringSnapshotRecord)))
        return first, second, rows

    first, second, rows = asyncio.run(exercise())
    assert len(first) == 3
    assert second == []
    assert len(rows) == 3
    assert max(row.window_end for row in rows) == datetime(2026, 9, 25, 12, 35, tzinfo=UTC)
    assert all(row.window_end <= now for row in rows)


def test_worker_failure_is_bounded_and_periodic_recovery_survives_broker_outage() -> None:
    async def seed_disabled() -> UUID:
        snapshot_id = await _seed(with_evaluations=False)
        async with SessionLocal() as session, session.begin():
            snapshot = await session.get(MonitoringSnapshotRecord, snapshot_id)
            assert snapshot is not None
            definition = await session.get(
                MonitoringDefinitionRecord, snapshot.monitoring_definition_id
            )
            assert definition is not None
            definition.is_enabled = False
        return snapshot_id

    snapshot_id = asyncio.run(seed_disabled())

    def unavailable(_: UUID) -> None:
        raise ConnectionError("redis unavailable")

    recovery_time = datetime.now(UTC) + timedelta(minutes=10)
    assert asyncio.run(recover_monitoring_once(enqueue=unavailable, now=recovery_time)) == 0
    sent: list[UUID] = []
    assert asyncio.run(recover_monitoring_once(enqueue=sent.append, now=recovery_time)) == 1
    assert sent == [snapshot_id]

    with patch(
        "agentscope_api.services.monitoring_orchestration.aggregate_snapshot",
        side_effect=RuntimeError("database read failed"),
    ):
        outcomes = [asyncio.run(process_monitoring_message(snapshot_id)) for _ in range(4)]
    assert outcomes == [
        MaterializationOutcome.REQUEUED,
        MaterializationOutcome.REQUEUED,
        MaterializationOutcome.REQUEUED,
        MaterializationOutcome.FAILED,
    ]

    async def verify() -> MonitoringSnapshotRecord:
        async with SessionLocal() as session:
            record = await session.get(MonitoringSnapshotRecord, snapshot_id)
            assert record is not None
            return record

    record = asyncio.run(verify())
    assert record.status == "failed"
    assert record.attempt_count == 4
    assert "database read failed" in str(record.error_message)
