from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.database import SessionLocal
from agentscope_api.models.automatic_drift import (
    AutomaticDriftCheckRecord,
    AutomaticDriftConfigurationRecord,
    MonitoringIncidentEventRecord,
    MonitoringIncidentRecord,
)
from agentscope_api.models.drift import DriftComparisonRecord, DriftPolicyRecord
from agentscope_api.models.monitoring import MonitoringDefinitionRecord, MonitoringSnapshotRecord
from agentscope_api.schemas.automatic_drift import (
    AutomaticDriftCheck,
    AutomaticDriftCheckList,
    AutomaticDriftCheckListParams,
    AutomaticDriftConfiguration,
    AutomaticDriftConfigurationCreate,
    AutomaticDriftConfigurationList,
    AutomaticDriftConfigurationListParams,
    AutomaticDriftConfigurationUpdate,
    MonitoringIncident,
    MonitoringIncidentEvent,
    MonitoringIncidentEventList,
    MonitoringIncidentEventListParams,
    MonitoringIncidentList,
    MonitoringIncidentListParams,
)
from agentscope_api.schemas.drift import DriftClassification, DriftComparisonCreate
from agentscope_api.services.drift import create_drift_comparison_in_transaction

MAX_ATTEMPTS = 4
MAX_SCAN_LIMIT = 500
LEASE_DURATION = timedelta(minutes=2)
QUEUE_RECOVERY_AGE = timedelta(minutes=5)


class AutomaticDriftOutcome(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    REQUEUED = "requeued"
    FAILED = "failed"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class AutomaticDriftClaim:
    check_id: UUID
    token: UUID
    attempt: int


class MonitoringDefinitionNotFound(Exception):
    pass


class DriftPolicyNotFound(Exception):
    pass


class AutomaticDriftConfigurationNotFound(Exception):
    pass


class MonitoringIncidentNotFound(Exception):
    pass


class MonitoringIncidentAlreadyResolved(Exception):
    pass


async def create_configuration(
    session: AsyncSession, payload: AutomaticDriftConfigurationCreate
) -> AutomaticDriftConfiguration:
    async with session.begin():
        if await session.get(MonitoringDefinitionRecord, payload.monitoring_definition_id) is None:
            raise MonitoringDefinitionNotFound
        if await session.get(DriftPolicyRecord, payload.drift_policy_id) is None:
            raise DriftPolicyNotFound
        record = AutomaticDriftConfigurationRecord(**payload.model_dump(mode="python"))
        session.add(record)
        await session.flush()
        return AutomaticDriftConfiguration.model_validate(record)


async def get_configuration(
    session: AsyncSession, configuration_id: UUID
) -> AutomaticDriftConfiguration | None:
    record = await session.get(AutomaticDriftConfigurationRecord, configuration_id)
    return None if record is None else AutomaticDriftConfiguration.model_validate(record)


async def list_configurations(
    session: AsyncSession, params: AutomaticDriftConfigurationListParams
) -> AutomaticDriftConfigurationList:
    statement = select(AutomaticDriftConfigurationRecord)
    for column, value in (
        (AutomaticDriftConfigurationRecord.is_enabled, params.is_enabled),
        (
            AutomaticDriftConfigurationRecord.monitoring_definition_id,
            params.monitoring_definition_id,
        ),
        (AutomaticDriftConfigurationRecord.drift_policy_id, params.drift_policy_id),
    ):
        if value is not None:
            statement = statement.where(column == value)
    records = list(
        await session.scalars(
            statement.order_by(
                AutomaticDriftConfigurationRecord.created_at.desc(),
                AutomaticDriftConfigurationRecord.id.desc(),
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    return AutomaticDriftConfigurationList(
        items=[
            AutomaticDriftConfiguration.model_validate(item) for item in records[: params.page_size]
        ],
        has_more=len(records) > params.page_size,
    )


async def update_configuration(
    session: AsyncSession,
    configuration_id: UUID,
    payload: AutomaticDriftConfigurationUpdate,
) -> AutomaticDriftConfiguration:
    async with session.begin():
        record = await session.get(
            AutomaticDriftConfigurationRecord, configuration_id, with_for_update=True
        )
        if record is None:
            raise AutomaticDriftConfigurationNotFound
        record.is_enabled = payload.is_enabled
        record.updated_at = datetime.now(UTC)
        await session.flush()
        return AutomaticDriftConfiguration.model_validate(record)


async def list_checks(
    session: AsyncSession, params: AutomaticDriftCheckListParams
) -> AutomaticDriftCheckList:
    statement = select(AutomaticDriftCheckRecord)
    for column, value in (
        (
            AutomaticDriftCheckRecord.automatic_drift_configuration_id,
            params.automatic_drift_configuration_id,
        ),
        (AutomaticDriftCheckRecord.current_snapshot_id, params.current_snapshot_id),
        (AutomaticDriftCheckRecord.status, params.status),
    ):
        if value is not None:
            statement = statement.where(column == value)
    records = list(
        await session.scalars(
            statement.order_by(
                AutomaticDriftCheckRecord.created_at.desc(), AutomaticDriftCheckRecord.id.desc()
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    return AutomaticDriftCheckList(
        items=[AutomaticDriftCheck.model_validate(item) for item in records[: params.page_size]],
        has_more=len(records) > params.page_size,
    )


async def get_check(session: AsyncSession, check_id: UUID) -> AutomaticDriftCheck | None:
    record = await session.get(AutomaticDriftCheckRecord, check_id)
    return None if record is None else AutomaticDriftCheck.model_validate(record)


async def list_incidents(
    session: AsyncSession, params: MonitoringIncidentListParams
) -> MonitoringIncidentList:
    statement = select(MonitoringIncidentRecord)
    for column, value in (
        (MonitoringIncidentRecord.monitoring_definition_id, params.monitoring_definition_id),
        (
            MonitoringIncidentRecord.automatic_drift_configuration_id,
            params.automatic_drift_configuration_id,
        ),
        (MonitoringIncidentRecord.status, params.status),
    ):
        if value is not None:
            statement = statement.where(column == value)
    records = list(
        await session.scalars(
            statement.order_by(
                MonitoringIncidentRecord.created_at.desc(), MonitoringIncidentRecord.id.desc()
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    return MonitoringIncidentList(
        items=[MonitoringIncident.model_validate(item) for item in records[: params.page_size]],
        has_more=len(records) > params.page_size,
    )


async def get_incident(session: AsyncSession, incident_id: UUID) -> MonitoringIncident | None:
    record = await session.get(MonitoringIncidentRecord, incident_id)
    return None if record is None else MonitoringIncident.model_validate(record)


async def list_incident_events(
    session: AsyncSession, incident_id: UUID, params: MonitoringIncidentEventListParams
) -> MonitoringIncidentEventList:
    if await session.get(MonitoringIncidentRecord, incident_id) is None:
        raise MonitoringIncidentNotFound
    records = list(
        await session.scalars(
            select(MonitoringIncidentEventRecord)
            .where(MonitoringIncidentEventRecord.incident_id == incident_id)
            .order_by(MonitoringIncidentEventRecord.created_at, MonitoringIncidentEventRecord.id)
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    return MonitoringIncidentEventList(
        items=[
            MonitoringIncidentEvent.model_validate(item) for item in records[: params.page_size]
        ],
        has_more=len(records) > params.page_size,
    )


async def acknowledge_incident(
    session: AsyncSession, incident_id: UUID, *, now: datetime | None = None
) -> MonitoringIncident:
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        incident = await session.get(MonitoringIncidentRecord, incident_id, with_for_update=True)
        if incident is None:
            raise MonitoringIncidentNotFound
        if incident.status == "resolved":
            raise MonitoringIncidentAlreadyResolved
        if incident.status == "open":
            incident.status = "acknowledged"
            incident.acknowledged_at = timestamp
            incident.updated_at = timestamp
            session.add(
                MonitoringIncidentEventRecord(
                    incident_id=incident.id,
                    automatic_drift_check_id=None,
                    drift_comparison_id=incident.latest_drift_comparison_id,
                    event_type="incident_acknowledged",
                    created_at=timestamp,
                )
            )
            await session.flush()
        return MonitoringIncident.model_validate(incident)


async def discover_automatic_checks(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 100
) -> list[UUID]:
    if not 1 <= limit <= MAX_SCAN_LIMIT:
        raise ValueError("invalid automatic drift scan limit")
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        configurations = list(
            await session.scalars(
                select(AutomaticDriftConfigurationRecord)
                .where(AutomaticDriftConfigurationRecord.is_enabled.is_(True))
                .order_by(AutomaticDriftConfigurationRecord.id)
            )
        )
        created: list[UUID] = []
        for configuration in configurations:
            if len(created) >= limit:
                break
            active = await session.scalar(
                select(
                    exists().where(
                        AutomaticDriftCheckRecord.automatic_drift_configuration_id
                        == configuration.id,
                        AutomaticDriftCheckRecord.status.in_(("pending", "queued", "running")),
                    )
                )
            )
            if active:
                continue
            current = await session.scalar(
                select(MonitoringSnapshotRecord)
                .where(
                    MonitoringSnapshotRecord.monitoring_definition_id
                    == configuration.monitoring_definition_id,
                    MonitoringSnapshotRecord.status == "completed",
                    ~exists().where(
                        AutomaticDriftCheckRecord.automatic_drift_configuration_id
                        == configuration.id,
                        AutomaticDriftCheckRecord.current_snapshot_id
                        == MonitoringSnapshotRecord.id,
                    ),
                )
                .order_by(MonitoringSnapshotRecord.window_start, MonitoringSnapshotRecord.id)
                .limit(1)
            )
            if current is None:
                continue
            duration = current.window_end - current.window_start
            baseline = await session.scalar(
                select(MonitoringSnapshotRecord).where(
                    MonitoringSnapshotRecord.monitoring_definition_id
                    == configuration.monitoring_definition_id,
                    MonitoringSnapshotRecord.window_start == current.window_start - duration,
                    MonitoringSnapshotRecord.window_end == current.window_start,
                )
            )
            check_id = await session.scalar(
                insert(AutomaticDriftCheckRecord)
                .values(
                    id=uuid4(),
                    automatic_drift_configuration_id=configuration.id,
                    baseline_snapshot_id=None if baseline is None else baseline.id,
                    current_snapshot_id=current.id,
                    status="queued",
                    queued_at=timestamp,
                )
                .on_conflict_do_nothing()
                .returning(AutomaticDriftCheckRecord.id)
            )
            if check_id is not None:
                created.append(check_id)
        return created


async def claim_automatic_check(
    session: AsyncSession, check_id: UUID, *, now: datetime | None = None
) -> AutomaticDriftClaim | None:
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        record = await session.get(AutomaticDriftCheckRecord, check_id, with_for_update=True)
        if record is None or record.status != "queued" or record.attempt_count >= MAX_ATTEMPTS:
            return None
        token = uuid4()
        record.status = "running"
        record.attempt_count += 1
        record.lease_token = token
        record.lease_expires_at = timestamp + LEASE_DURATION
        record.heartbeat_at = timestamp
        record.started_at = record.started_at or timestamp
        return AutomaticDriftClaim(record.id, token, record.attempt_count)


def _finish_check(
    check: AutomaticDriftCheckRecord,
    *,
    status: str,
    timestamp: datetime,
    skip_reason: str | None = None,
    drift_comparison_id: UUID | None = None,
) -> None:
    check.status = status
    check.skip_reason = skip_reason
    check.drift_comparison_id = drift_comparison_id
    check.lease_token = None
    check.lease_expires_at = None
    check.heartbeat_at = timestamp
    check.completed_at = timestamp


async def _apply_incident(
    session: AsyncSession,
    configuration: AutomaticDriftConfigurationRecord,
    check: AutomaticDriftCheckRecord,
    comparison: DriftComparisonRecord,
    timestamp: datetime,
) -> None:
    incident = await session.scalar(
        select(MonitoringIncidentRecord)
        .where(
            MonitoringIncidentRecord.automatic_drift_configuration_id == configuration.id,
            MonitoringIncidentRecord.status.in_(("open", "acknowledged")),
        )
        .with_for_update()
    )
    if comparison.classification == DriftClassification.DRIFT_DETECTED:
        if incident is None:
            incident = MonitoringIncidentRecord(
                automatic_drift_configuration_id=configuration.id,
                monitoring_definition_id=configuration.monitoring_definition_id,
                drift_policy_id=configuration.drift_policy_id,
                status="open",
                opened_at=timestamp,
                first_drift_comparison_id=comparison.id,
                latest_drift_comparison_id=comparison.id,
                latest_classification=comparison.classification,
                occurrence_count=1,
                consecutive_clean_count=0,
                last_drift_event_at=timestamp,
            )
            session.add(incident)
            await session.flush()
            session.add(
                MonitoringIncidentEventRecord(
                    incident_id=incident.id,
                    automatic_drift_check_id=check.id,
                    drift_comparison_id=comparison.id,
                    event_type="incident_opened",
                    created_at=timestamp,
                )
            )
            return
        incident.latest_drift_comparison_id = comparison.id
        incident.latest_classification = comparison.classification
        incident.occurrence_count += 1
        incident.consecutive_clean_count = 0
        incident.updated_at = timestamp
        if timestamp >= incident.last_drift_event_at + timedelta(
            seconds=configuration.cooldown_seconds
        ):
            incident.last_drift_event_at = timestamp
            session.add(
                MonitoringIncidentEventRecord(
                    incident_id=incident.id,
                    automatic_drift_check_id=check.id,
                    drift_comparison_id=comparison.id,
                    event_type="drift_reoccurred",
                    created_at=timestamp,
                )
            )
        else:
            check.event_suppressed = True
            check.event_suppression_reason = "cooldown"
        return
    if incident is None:
        return
    incident.latest_classification = comparison.classification
    incident.updated_at = timestamp
    if comparison.classification != DriftClassification.NO_DRIFT_DETECTED:
        return
    incident.consecutive_clean_count += 1
    if incident.consecutive_clean_count < configuration.resolve_after_clean_windows:
        return
    incident.status = "resolved"
    incident.resolved_at = timestamp
    incident.resolving_comparison_id = comparison.id
    session.add(
        MonitoringIncidentEventRecord(
            incident_id=incident.id,
            automatic_drift_check_id=check.id,
            drift_comparison_id=comparison.id,
            event_type="incident_resolved",
            created_at=timestamp,
        )
    )


async def finalize_automatic_check(
    claim: AutomaticDriftClaim, *, now: datetime | None = None
) -> AutomaticDriftOutcome:
    timestamp = now or datetime.now(UTC)
    async with SessionLocal() as session, session.begin():
        check = await session.get(AutomaticDriftCheckRecord, claim.check_id, with_for_update=True)
        if (
            check is None
            or check.status != "running"
            or check.lease_token != claim.token
            or check.attempt_count != claim.attempt
            or check.lease_expires_at is None
            or check.lease_expires_at <= timestamp
        ):
            return AutomaticDriftOutcome.IGNORED
        configuration = await session.get(
            AutomaticDriftConfigurationRecord,
            check.automatic_drift_configuration_id,
            with_for_update=True,
        )
        current = await session.get(MonitoringSnapshotRecord, check.current_snapshot_id)
        if (
            configuration is None
            or await session.get(DriftPolicyRecord, configuration.drift_policy_id) is None
        ):
            _finish_check(
                check, status="skipped", skip_reason="policy_unavailable", timestamp=timestamp
            )
            return AutomaticDriftOutcome.SKIPPED
        if current is None or current.status != "completed":
            _finish_check(
                check,
                status="skipped",
                skip_reason="current_snapshot_incomplete",
                timestamp=timestamp,
            )
            return AutomaticDriftOutcome.SKIPPED
        duration = current.window_end - current.window_start
        baseline = await session.scalar(
            select(MonitoringSnapshotRecord).where(
                MonitoringSnapshotRecord.monitoring_definition_id
                == configuration.monitoring_definition_id,
                MonitoringSnapshotRecord.window_start == current.window_start - duration,
                MonitoringSnapshotRecord.window_end == current.window_start,
            )
        )
        if baseline is None:
            _finish_check(
                check,
                status="skipped",
                skip_reason="baseline_snapshot_missing",
                timestamp=timestamp,
            )
            return AutomaticDriftOutcome.SKIPPED
        check.baseline_snapshot_id = baseline.id
        if baseline.status != "completed":
            _finish_check(
                check, status="skipped", skip_reason="baseline_incomplete", timestamp=timestamp
            )
            return AutomaticDriftOutcome.SKIPPED
        comparison = await session.scalar(
            select(DriftComparisonRecord).where(
                DriftComparisonRecord.drift_policy_id == configuration.drift_policy_id,
                DriftComparisonRecord.baseline_snapshot_id == baseline.id,
                DriftComparisonRecord.current_snapshot_id == current.id,
            )
        )
        if comparison is None:
            result = await create_drift_comparison_in_transaction(
                session,
                DriftComparisonCreate(
                    drift_policy_id=configuration.drift_policy_id,
                    baseline_snapshot_id=baseline.id,
                    current_snapshot_id=current.id,
                ),
            )
            comparison = await session.get(DriftComparisonRecord, result.id)
            assert comparison is not None
        await _apply_incident(session, configuration, check, comparison, timestamp)
        _finish_check(
            check, status="completed", drift_comparison_id=comparison.id, timestamp=timestamp
        )
        return AutomaticDriftOutcome.COMPLETED


async def _handle_failure(
    claim: AutomaticDriftClaim, error: Exception, *, now: datetime | None = None
) -> AutomaticDriftOutcome:
    timestamp = now or datetime.now(UTC)
    async with SessionLocal() as session, session.begin():
        check = await session.get(AutomaticDriftCheckRecord, claim.check_id, with_for_update=True)
        if (
            check is None
            or check.status != "running"
            or check.lease_token != claim.token
            or check.attempt_count != claim.attempt
        ):
            return AutomaticDriftOutcome.IGNORED
        check.lease_token = None
        check.lease_expires_at = None
        check.heartbeat_at = timestamp
        if check.attempt_count < MAX_ATTEMPTS:
            check.status = "queued"
            check.last_enqueued_at = None
            return AutomaticDriftOutcome.REQUEUED
        check.status = "failed"
        check.failure_reason = "comparison_failure"
        check.error_message = f"{type(error).__name__}: {error}"[:4_000]
        check.completed_at = timestamp
        return AutomaticDriftOutcome.FAILED


async def process_automatic_drift_message(check_id: UUID) -> AutomaticDriftOutcome:
    async with SessionLocal() as session:
        claim = await claim_automatic_check(session, check_id)
    if claim is None:
        return AutomaticDriftOutcome.IGNORED
    try:
        return await finalize_automatic_check(claim)
    except Exception as error:
        return await _handle_failure(claim, error)


async def recover_automatic_checks(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 100
) -> list[UUID]:
    if not 1 <= limit <= MAX_SCAN_LIMIT:
        raise ValueError("invalid automatic drift recovery limit")
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        records = list(
            await session.scalars(
                select(AutomaticDriftCheckRecord)
                .where(
                    or_(
                        (AutomaticDriftCheckRecord.status == "queued")
                        & (
                            AutomaticDriftCheckRecord.last_enqueued_at.is_(None)
                            | (
                                AutomaticDriftCheckRecord.last_enqueued_at
                                <= timestamp - QUEUE_RECOVERY_AGE
                            )
                        ),
                        (AutomaticDriftCheckRecord.status == "running")
                        & (AutomaticDriftCheckRecord.lease_expires_at <= timestamp),
                    )
                )
                .order_by(AutomaticDriftCheckRecord.created_at, AutomaticDriftCheckRecord.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        recovered: list[UUID] = []
        for record in records:
            if record.attempt_count >= MAX_ATTEMPTS:
                record.status = "failed"
                record.failure_reason = "attempt_limit_exceeded"
                record.error_message = "automatic drift check attempt limit exceeded"
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
    session: AsyncSession, check_id: UUID, reserved_at: datetime
) -> None:
    async with session.begin():
        await session.execute(
            update(AutomaticDriftCheckRecord)
            .where(
                AutomaticDriftCheckRecord.id == check_id,
                AutomaticDriftCheckRecord.status == "queued",
                AutomaticDriftCheckRecord.last_enqueued_at == reserved_at,
            )
            .values(last_enqueued_at=None)
        )
