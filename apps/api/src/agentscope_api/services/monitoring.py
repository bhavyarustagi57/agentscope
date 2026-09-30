from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.models.evaluation import EvaluationDefinitionRecord
from agentscope_api.models.monitoring import MonitoringDefinitionRecord, MonitoringSnapshotRecord
from agentscope_api.schemas.monitoring import (
    MonitoringDefinition,
    MonitoringDefinitionCreate,
    MonitoringDefinitionList,
    MonitoringDefinitionListParams,
    MonitoringSnapshot,
    MonitoringSnapshotList,
    MonitoringSnapshotListParams,
    MonitoringTraceScope,
    WindowDuration,
)


class MonitoringDefinitionNotFound(Exception):
    pass


class EvaluationDefinitionNotFound(Exception):
    pass


class InvalidMonitoringWindow(Exception):
    pass


def _utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("window timestamps must include a UTC offset")
    return value.astimezone(UTC)


def aligned_window(now: datetime, duration_seconds: int) -> tuple[datetime, datetime]:
    timestamp = _utc(now)
    boundary = int(timestamp.timestamp()) // duration_seconds * duration_seconds
    end = datetime.fromtimestamp(boundary, UTC)
    start = datetime.fromtimestamp(boundary - duration_seconds, UTC)
    return start, end


def validate_closed_window(
    start: datetime, end: datetime, duration_seconds: int, now: datetime
) -> tuple[datetime, datetime]:
    window_start, window_end, timestamp = _utc(start), _utc(end), _utc(now)
    if (
        int(window_start.timestamp()) % duration_seconds
        or (window_end - window_start).total_seconds() != duration_seconds
    ):
        raise ValueError("window must use aligned UTC boundaries")
    if window_end > timestamp:
        raise ValueError("window must be fully closed")
    return window_start, window_end


def _definition(record: MonitoringDefinitionRecord) -> MonitoringDefinition:
    return MonitoringDefinition(
        id=record.id,
        name=record.name,
        description=record.description,
        is_enabled=record.is_enabled,
        window_duration=WindowDuration.from_seconds(record.window_duration_seconds),
        trace_scope=MonitoringTraceScope(
            trace_name=record.trace_name,
            trace_status=record.trace_status,
        ),
        evaluation_definition_id=record.evaluation_definition_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


async def create_definition(
    session: AsyncSession, payload: MonitoringDefinitionCreate
) -> MonitoringDefinition:
    async with session.begin():
        if (
            payload.evaluation_definition_id is not None
            and await session.get(EvaluationDefinitionRecord, payload.evaluation_definition_id)
            is None
        ):
            raise EvaluationDefinitionNotFound
        record = MonitoringDefinitionRecord(
            name=payload.name,
            description=payload.description,
            is_enabled=payload.is_enabled,
            window_duration_seconds=payload.window_duration.seconds,
            trace_name=payload.trace_scope.trace_name,
            trace_status=payload.trace_scope.trace_status.value
            if payload.trace_scope.trace_status is not None
            else None,
            evaluation_definition_id=payload.evaluation_definition_id,
        )
        session.add(record)
    await session.refresh(record)
    return _definition(record)


async def get_definition(session: AsyncSession, definition_id: UUID) -> MonitoringDefinition | None:
    record = await session.get(MonitoringDefinitionRecord, definition_id)
    return None if record is None else _definition(record)


async def list_definitions(
    session: AsyncSession, params: MonitoringDefinitionListParams
) -> MonitoringDefinitionList:
    statement = select(MonitoringDefinitionRecord)
    if params.is_enabled is not None:
        statement = statement.where(MonitoringDefinitionRecord.is_enabled == params.is_enabled)
    rows = list(
        await session.scalars(
            statement.order_by(
                MonitoringDefinitionRecord.created_at.desc(), MonitoringDefinitionRecord.id.desc()
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    return MonitoringDefinitionList(
        items=[_definition(row) for row in rows[: params.page_size]],
        has_more=len(rows) > params.page_size,
    )


async def set_definition_enabled(
    session: AsyncSession, definition_id: UUID, is_enabled: bool
) -> MonitoringDefinition:
    async with session.begin():
        record = await session.get(MonitoringDefinitionRecord, definition_id, with_for_update=True)
        if record is None:
            raise MonitoringDefinitionNotFound
        record.is_enabled = is_enabled
        record.updated_at = datetime.now(UTC)
    return _definition(record)


async def create_snapshot_intent(
    session: AsyncSession,
    definition_id: UUID,
    *,
    window_start: datetime | None,
    window_end: datetime | None,
    now: datetime | None = None,
) -> tuple[MonitoringSnapshotRecord, bool]:
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        definition = await session.get(MonitoringDefinitionRecord, definition_id)
        if definition is None:
            raise MonitoringDefinitionNotFound
        if window_start is None or window_end is None:
            start, end = aligned_window(timestamp, definition.window_duration_seconds)
        else:
            try:
                start, end = validate_closed_window(
                    window_start, window_end, definition.window_duration_seconds, timestamp
                )
            except ValueError as error:
                raise InvalidMonitoringWindow(str(error)) from error
        snapshot_id = await session.scalar(
            insert(MonitoringSnapshotRecord)
            .values(
                id=uuid4(),
                monitoring_definition_id=definition_id,
                window_start=start,
                window_end=end,
                status="queued",
                queued_at=timestamp,
            )
            .on_conflict_do_nothing(constraint="uq_monitoring_snapshots_window")
            .returning(MonitoringSnapshotRecord.id)
        )
        created = snapshot_id is not None
        if snapshot_id is None:
            snapshot_id = await session.scalar(
                select(MonitoringSnapshotRecord.id).where(
                    MonitoringSnapshotRecord.monitoring_definition_id == definition_id,
                    MonitoringSnapshotRecord.window_start == start,
                    MonitoringSnapshotRecord.window_end == end,
                )
            )
        assert snapshot_id is not None
        record = await session.get(MonitoringSnapshotRecord, snapshot_id)
        assert record is not None
    return record, created


async def mark_snapshot_enqueued(session: AsyncSession, snapshot_id: UUID) -> None:
    async with session.begin():
        await session.execute(
            update(MonitoringSnapshotRecord)
            .where(
                MonitoringSnapshotRecord.id == snapshot_id,
                MonitoringSnapshotRecord.status == "queued",
            )
            .values(last_enqueued_at=datetime.now(UTC))
        )


def snapshot_response(record: MonitoringSnapshotRecord) -> MonitoringSnapshot:
    return MonitoringSnapshot.model_validate(record)


async def get_snapshot(session: AsyncSession, snapshot_id: UUID) -> MonitoringSnapshot | None:
    record = await session.get(MonitoringSnapshotRecord, snapshot_id)
    return None if record is None else snapshot_response(record)


async def list_snapshots(
    session: AsyncSession,
    definition_id: UUID,
    params: MonitoringSnapshotListParams,
) -> MonitoringSnapshotList:
    if await session.get(MonitoringDefinitionRecord, definition_id) is None:
        raise MonitoringDefinitionNotFound
    statement = select(MonitoringSnapshotRecord).where(
        MonitoringSnapshotRecord.monitoring_definition_id == definition_id
    )
    if params.status is not None:
        statement = statement.where(MonitoringSnapshotRecord.status == params.status.value)
    if params.window_start_gte is not None:
        statement = statement.where(
            MonitoringSnapshotRecord.window_start >= _utc(params.window_start_gte)
        )
    if params.window_end_lte is not None:
        statement = statement.where(
            MonitoringSnapshotRecord.window_end <= _utc(params.window_end_lte)
        )
    rows = list(
        await session.scalars(
            statement.order_by(
                MonitoringSnapshotRecord.window_start.desc(), MonitoringSnapshotRecord.id.desc()
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    return MonitoringSnapshotList(
        items=[snapshot_response(row) for row in rows[: params.page_size]],
        has_more=len(rows) > params.page_size,
    )
