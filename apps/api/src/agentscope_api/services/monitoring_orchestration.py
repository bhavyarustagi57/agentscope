from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Float, Integer, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.database import SessionLocal
from agentscope_api.models.evaluation import EvaluationResultRecord, EvaluationRunRecord
from agentscope_api.models.monitoring import MonitoringDefinitionRecord, MonitoringSnapshotRecord
from agentscope_api.models.trace import SpanRecord, TraceRecord
from agentscope_api.services.monitoring import aligned_window

MAX_ATTEMPTS = 4
MAX_RECOVERY_BATCH = 100
LEASE_DURATION = timedelta(minutes=2)
QUEUE_RECOVERY_AGE = timedelta(minutes=5)


class MaterializationOutcome(StrEnum):
    COMPLETED = "completed"
    REQUEUED = "requeued"
    FAILED = "failed"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class SnapshotClaim:
    snapshot_id: UUID
    token: UUID
    attempt: int


_ZERO_METRICS: dict[str, int | float | None] = {
    "trace_count": 0,
    "successful_trace_count": 0,
    "failed_trace_count": 0,
    "success_rate": None,
    "failure_rate": None,
    "duration_sample_count": 0,
    "mean_duration_ms": None,
    "median_duration_ms": None,
    "p95_duration_ms": None,
    "token_sample_count": 0,
    "input_tokens": None,
    "output_tokens": None,
    "total_tokens": None,
    "mean_total_tokens": None,
    "evaluated_result_count": 0,
    "passed_evaluation_count": 0,
    "failed_evaluation_count": 0,
    "evaluator_error_count": 0,
    "valid_binary_evaluation_count": 0,
    "evaluation_pass_rate": None,
    "evaluation_error_rate": None,
}


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) else None


async def claim_snapshot(
    session: AsyncSession, snapshot_id: UUID, *, now: datetime | None = None
) -> SnapshotClaim | None:
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        record = await session.get(MonitoringSnapshotRecord, snapshot_id, with_for_update=True)
        if record is None or record.status != "queued" or record.attempt_count >= MAX_ATTEMPTS:
            return None
        token = uuid4()
        record.status = "running"
        record.attempt_count += 1
        record.lease_token = token
        record.lease_expires_at = timestamp + LEASE_DURATION
        record.heartbeat_at = timestamp
        record.started_at = record.started_at or timestamp
        return SnapshotClaim(record.id, token, record.attempt_count)


def _trace_statement(
    definition: MonitoringDefinitionRecord, snapshot: MonitoringSnapshotRecord
) -> Any:
    input_tokens = cast(SpanRecord.llm["token_usage"]["input_tokens"].astext, Integer)
    output_tokens = cast(SpanRecord.llm["token_usage"]["output_tokens"].astext, Integer)
    total_tokens = cast(SpanRecord.llm["token_usage"]["total_tokens"].astext, Integer)
    tokens = (
        select(
            SpanRecord.trace_id.label("trace_id"),
            func.sum(input_tokens).label("input_tokens"),
            func.sum(output_tokens).label("output_tokens"),
            func.sum(total_tokens).label("total_tokens"),
        )
        .where(SpanRecord.llm.is_not(None))
        .group_by(SpanRecord.trace_id)
        .subquery()
    )
    statement = (
        select(
            func.count(TraceRecord.trace_id).label("trace_count"),
            func.count().filter(TraceRecord.status == "success").label("successful_trace_count"),
            func.count().filter(TraceRecord.status == "error").label("failed_trace_count"),
            func.count(TraceRecord.duration_ms).label("duration_sample_count"),
            func.avg(TraceRecord.duration_ms).label("mean_duration_ms"),
            func.percentile_cont(0.5)
            .within_group(TraceRecord.duration_ms)
            .label("median_duration_ms"),
            func.percentile_cont(0.95)
            .within_group(TraceRecord.duration_ms)
            .label("p95_duration_ms"),
            func.count(tokens.c.total_tokens).label("token_sample_count"),
            func.sum(tokens.c.input_tokens).label("input_tokens"),
            func.sum(tokens.c.output_tokens).label("output_tokens"),
            func.sum(tokens.c.total_tokens).label("total_tokens"),
            func.avg(cast(tokens.c.total_tokens, Float)).label("mean_total_tokens"),
        )
        .select_from(TraceRecord)
        .outerjoin(tokens, tokens.c.trace_id == TraceRecord.trace_id)
        .where(
            TraceRecord.started_at >= snapshot.window_start,
            TraceRecord.started_at < snapshot.window_end,
        )
    )
    if definition.trace_name is not None:
        statement = statement.where(TraceRecord.name == definition.trace_name)
    if definition.trace_status is not None:
        statement = statement.where(TraceRecord.status == definition.trace_status)
    return statement


async def aggregate_snapshot(snapshot_id: UUID) -> dict[str, int | float | None]:
    async with SessionLocal() as session:
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        snapshot = await session.get(MonitoringSnapshotRecord, snapshot_id)
        if snapshot is None:
            raise LookupError("monitoring snapshot was not found")
        definition = await session.get(
            MonitoringDefinitionRecord, snapshot.monitoring_definition_id
        )
        if definition is None:
            raise LookupError("monitoring definition was not found")
        trace_row = (await session.execute(_trace_statement(definition, snapshot))).one()
        metrics = dict(_ZERO_METRICS)
        trace_count = int(trace_row.trace_count)
        successful = int(trace_row.successful_trace_count)
        failed = int(trace_row.failed_trace_count)
        metrics.update(
            trace_count=trace_count,
            successful_trace_count=successful,
            failed_trace_count=failed,
            success_rate=successful / trace_count if trace_count else None,
            failure_rate=failed / trace_count if trace_count else None,
            duration_sample_count=int(trace_row.duration_sample_count),
            mean_duration_ms=_finite(trace_row.mean_duration_ms),
            median_duration_ms=_finite(trace_row.median_duration_ms),
            p95_duration_ms=_finite(trace_row.p95_duration_ms),
            token_sample_count=int(trace_row.token_sample_count),
            input_tokens=int(trace_row.input_tokens)
            if trace_row.input_tokens is not None
            else None,
            output_tokens=int(trace_row.output_tokens)
            if trace_row.output_tokens is not None
            else None,
            total_tokens=int(trace_row.total_tokens)
            if trace_row.total_tokens is not None
            else None,
            mean_total_tokens=_finite(trace_row.mean_total_tokens),
        )
        if definition.evaluation_definition_id is not None:
            evaluation = (
                await session.execute(
                    select(
                        func.count(EvaluationResultRecord.id).label("evaluated"),
                        func.count()
                        .filter(EvaluationResultRecord.outcome == "passed")
                        .label("passed"),
                        func.count()
                        .filter(EvaluationResultRecord.outcome == "failed")
                        .label("failed"),
                        func.count()
                        .filter(EvaluationResultRecord.outcome == "error")
                        .label("errors"),
                    )
                    .join(
                        EvaluationRunRecord, EvaluationRunRecord.id == EvaluationResultRecord.run_id
                    )
                    .where(
                        EvaluationRunRecord.definition_id == definition.evaluation_definition_id,
                        EvaluationRunRecord.status == "completed",
                        EvaluationResultRecord.created_at >= snapshot.window_start,
                        EvaluationResultRecord.created_at < snapshot.window_end,
                    )
                )
            ).one()
            evaluated, passed, failed_evaluations, errors = map(
                int, (evaluation.evaluated, evaluation.passed, evaluation.failed, evaluation.errors)
            )
            valid = passed + failed_evaluations
            metrics.update(
                evaluated_result_count=evaluated,
                passed_evaluation_count=passed,
                failed_evaluation_count=failed_evaluations,
                evaluator_error_count=errors,
                valid_binary_evaluation_count=valid,
                evaluation_pass_rate=passed / valid if valid else None,
                evaluation_error_rate=errors / evaluated if evaluated else None,
            )
        return metrics


async def finalize_snapshot(
    claim: SnapshotClaim,
    metrics: Mapping[str, int | float | None],
    *,
    now: datetime | None = None,
) -> bool:
    timestamp = now or datetime.now(UTC)
    values = {**_ZERO_METRICS, **metrics}
    async with SessionLocal() as session, session.begin():
        result = await session.execute(
            update(MonitoringSnapshotRecord)
            .where(
                MonitoringSnapshotRecord.id == claim.snapshot_id,
                MonitoringSnapshotRecord.status == "running",
                MonitoringSnapshotRecord.lease_token == claim.token,
                MonitoringSnapshotRecord.attempt_count == claim.attempt,
                MonitoringSnapshotRecord.lease_expires_at > timestamp,
            )
            .values(
                **values,
                status="completed",
                lease_token=None,
                lease_expires_at=None,
                heartbeat_at=timestamp,
                completed_at=timestamp,
            )
        )
        return bool(getattr(result, "rowcount", 0))


async def _handle_failure(claim: SnapshotClaim, error: Exception) -> MaterializationOutcome:
    timestamp = datetime.now(UTC)
    async with SessionLocal() as session, session.begin():
        record = await session.get(
            MonitoringSnapshotRecord, claim.snapshot_id, with_for_update=True
        )
        if (
            record is None
            or record.status != "running"
            or record.lease_token != claim.token
            or record.attempt_count != claim.attempt
        ):
            return MaterializationOutcome.IGNORED
        record.lease_token = None
        record.lease_expires_at = None
        record.heartbeat_at = timestamp
        if record.attempt_count < MAX_ATTEMPTS:
            record.status = "queued"
            record.last_enqueued_at = None
            return MaterializationOutcome.REQUEUED
        record.status = "failed"
        record.error_message = f"{type(error).__name__}: {error}"[:4_000]
        record.completed_at = timestamp
        return MaterializationOutcome.FAILED


async def process_monitoring_message(snapshot_id: UUID) -> MaterializationOutcome:
    async with SessionLocal() as session:
        claim = await claim_snapshot(session, snapshot_id)
    if claim is None:
        return MaterializationOutcome.IGNORED
    try:
        metrics = await aggregate_snapshot(snapshot_id)
        completed = await finalize_snapshot(claim, metrics)
    except Exception as error:
        return await _handle_failure(claim, error)
    return MaterializationOutcome.COMPLETED if completed else MaterializationOutcome.IGNORED


async def recover_snapshots(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = MAX_RECOVERY_BATCH,
) -> list[UUID]:
    if not 1 <= limit <= MAX_RECOVERY_BATCH:
        raise ValueError("invalid recovery limit")
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        records = list(
            await session.scalars(
                select(MonitoringSnapshotRecord)
                .where(
                    or_(
                        (MonitoringSnapshotRecord.status == "queued")
                        & (
                            MonitoringSnapshotRecord.last_enqueued_at.is_(None)
                            | (
                                MonitoringSnapshotRecord.last_enqueued_at
                                <= timestamp - QUEUE_RECOVERY_AGE
                            )
                        ),
                        (MonitoringSnapshotRecord.status == "running")
                        & (MonitoringSnapshotRecord.lease_expires_at <= timestamp),
                    )
                )
                .order_by(MonitoringSnapshotRecord.created_at, MonitoringSnapshotRecord.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        recovered: list[UUID] = []
        for record in records:
            if record.attempt_count >= MAX_ATTEMPTS:
                record.status = "failed"
                record.error_message = "monitoring materialization attempt limit exceeded"
                record.completed_at = timestamp
                record.lease_token = None
                record.lease_expires_at = None
                continue
            if record.status == "running":
                record.status = "queued"
                record.lease_token = None
                record.lease_expires_at = None
            record.last_enqueued_at = timestamp
            recovered.append(record.id)
        return recovered


async def release_recovery_reservation(
    session: AsyncSession, snapshot_id: UUID, reserved_at: datetime
) -> None:
    async with session.begin():
        await session.execute(
            update(MonitoringSnapshotRecord)
            .where(
                MonitoringSnapshotRecord.id == snapshot_id,
                MonitoringSnapshotRecord.status == "queued",
                MonitoringSnapshotRecord.last_enqueued_at == reserved_at,
            )
            .values(last_enqueued_at=None)
        )


async def discover_missing_snapshots(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    max_windows: int = 24,
) -> list[UUID]:
    if not 1 <= max_windows <= 168:
        raise ValueError("invalid monitoring catch-up limit")
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        definitions = list(
            await session.scalars(
                select(MonitoringDefinitionRecord)
                .where(MonitoringDefinitionRecord.is_enabled.is_(True))
                .order_by(MonitoringDefinitionRecord.id)
            )
        )
        created: list[UUID] = []
        for definition in definitions:
            latest_start, _ = aligned_window(timestamp, definition.window_duration_seconds)
            duration = timedelta(seconds=definition.window_duration_seconds)
            values = [
                {
                    "id": uuid4(),
                    "monitoring_definition_id": definition.id,
                    "window_start": latest_start - duration * offset,
                    "window_end": latest_start - duration * offset + duration,
                    "status": "queued",
                    "queued_at": timestamp,
                }
                for offset in reversed(range(max_windows))
            ]
            created.extend(
                list(
                    await session.scalars(
                        insert(MonitoringSnapshotRecord)
                        .values(values)
                        .on_conflict_do_nothing(constraint="uq_monitoring_snapshots_window")
                        .returning(MonitoringSnapshotRecord.id)
                    )
                )
            )
        return created
