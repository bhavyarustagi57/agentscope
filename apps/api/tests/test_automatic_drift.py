from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from agentscope_api.database import SessionLocal
from agentscope_api.jobs.recover import recover_automatic_drift_once
from agentscope_api.main import create_app
from agentscope_api.models.automatic_drift import (
    AutomaticDriftCheckRecord,
    AutomaticDriftConfigurationRecord,
    MonitoringIncidentEventRecord,
    MonitoringIncidentRecord,
)
from agentscope_api.models.drift import (
    DriftComparisonRecord,
    DriftPolicyRecord,
    DriftPolicyRuleRecord,
)
from agentscope_api.models.monitoring import MonitoringDefinitionRecord, MonitoringSnapshotRecord
from agentscope_api.schemas.automatic_drift import AutomaticDriftConfigurationCreate
from agentscope_api.services import automatic_drift as service

pytestmark = pytest.mark.usefixtures("clean_database")

START = datetime(2026, 9, 25, 0, tzinfo=UTC)


async def _seed_domain(
    *, cooldown_seconds: int = 3_600, resolve_after: int = 2, enabled: bool = True
) -> tuple[UUID, UUID, UUID]:
    async with SessionLocal() as session, session.begin():
        monitor = MonitoringDefinitionRecord(
            name="hourly checkout",
            description=None,
            is_enabled=True,
            window_duration_seconds=3_600,
            trace_name=None,
            trace_status=None,
            evaluation_definition_id=None,
        )
        policy = DriftPolicyRecord(name="failure increase", description=None)
        session.add_all([monitor, policy])
        await session.flush()
        session.add(
            DriftPolicyRuleRecord(
                drift_policy_id=policy.id,
                position=0,
                metric="trace_failure_rate",
                direction="increase",
                threshold_type="absolute",
                practical_threshold=0.1,
                minimum_baseline_samples=1,
                minimum_current_samples=1,
            )
        )
        configuration = AutomaticDriftConfigurationRecord(
            name="automatic checkout drift",
            monitoring_definition_id=monitor.id,
            drift_policy_id=policy.id,
            is_enabled=enabled,
            baseline_strategy="previous_window",
            cooldown_seconds=cooldown_seconds,
            resolve_after_clean_windows=resolve_after,
        )
        session.add(configuration)
        await session.flush()
        return monitor.id, policy.id, configuration.id


async def _snapshot(
    monitor_id: UUID,
    offset: int,
    failure_rate: float | None,
    *,
    status: str = "completed",
) -> UUID:
    count = 0 if failure_rate is None else 100
    failed = 0 if failure_rate is None else round(failure_rate * count)
    completed_at = START + timedelta(hours=offset + 1) if status == "completed" else None
    async with SessionLocal() as session, session.begin():
        record = MonitoringSnapshotRecord(
            monitoring_definition_id=monitor_id,
            window_start=START + timedelta(hours=offset),
            window_end=START + timedelta(hours=offset + 1),
            status=status,
            trace_count=count,
            successful_trace_count=count - failed,
            failed_trace_count=failed,
            success_rate=None if failure_rate is None else 1 - failure_rate,
            failure_rate=failure_rate,
            completed_at=completed_at,
            queued_at=START,
        )
        session.add(record)
        await session.flush()
        return record.id


async def _queue_and_run(configuration_id: UUID, current_id: UUID, now: datetime) -> UUID:
    async with SessionLocal() as session, session.begin():
        check = AutomaticDriftCheckRecord(
            automatic_drift_configuration_id=configuration_id,
            current_snapshot_id=current_id,
            status="queued",
            queued_at=now,
        )
        session.add(check)
        await session.flush()
        check_id = check.id
    async with SessionLocal() as session:
        claim = await service.claim_automatic_check(session, check_id, now=now)
    assert claim is not None
    assert await service.finalize_automatic_check(claim, now=now + timedelta(seconds=1)) in {
        service.AutomaticDriftOutcome.COMPLETED,
        service.AutomaticDriftOutcome.SKIPPED,
    }
    return check_id


async def test_configuration_crud_and_discovery_are_bounded_disabled_and_idempotent() -> None:
    monitor_id, policy_id, disabled_id = await _seed_domain(enabled=False)
    await _snapshot(monitor_id, 0, 0.0)
    async with SessionLocal() as session:
        assert (
            await service.discover_automatic_checks(session, now=START + timedelta(hours=2)) == []
        )

    async with SessionLocal() as session:
        created = await service.create_configuration(
            session,
            AutomaticDriftConfigurationCreate(
                name="enabled",
                monitoring_definition_id=monitor_id,
                drift_policy_id=policy_id,
            ),
        )
    async with SessionLocal() as session:
        first = await service.discover_automatic_checks(session, limit=1)
    async with SessionLocal() as session:
        second = await service.discover_automatic_checks(session, limit=1)
    assert len(first) == 1
    assert second == []
    assert created.id != disabled_id


@pytest.mark.parametrize(
    ("baseline_status", "expected_reason"),
    ((None, "baseline_snapshot_missing"), ("queued", "baseline_incomplete")),
)
async def test_previous_window_requires_exact_completed_adjacent_baseline(
    baseline_status: str | None, expected_reason: str
) -> None:
    monitor_id, _, configuration_id = await _seed_domain()
    if baseline_status is not None:
        await _snapshot(monitor_id, 0, 0.0, status=baseline_status)
    current_id = await _snapshot(monitor_id, 1, 0.2)
    check_id = await _queue_and_run(configuration_id, current_id, START + timedelta(hours=3))
    async with SessionLocal() as session:
        check = await session.get(AutomaticDriftCheckRecord, check_id)
    assert check is not None
    assert check.status == "skipped"
    assert check.skip_reason == expected_reason
    assert check.drift_comparison_id is None


async def test_worker_reuses_prompt2_opens_incident_and_duplicate_delivery_is_safe() -> None:
    monitor_id, _, configuration_id = await _seed_domain()
    await _snapshot(monitor_id, 0, 0.0)
    current_id = await _snapshot(monitor_id, 1, 0.2)
    original = service.create_drift_comparison_in_transaction
    with patch.object(service, "create_drift_comparison_in_transaction", wraps=original) as reused:
        check_id = await _queue_and_run(configuration_id, current_id, START + timedelta(hours=3))
    assert reused.await_count == 1
    assert (
        await service.process_automatic_drift_message(check_id)
        is service.AutomaticDriftOutcome.IGNORED
    )
    async with SessionLocal() as session:
        incident = await session.scalar(select(MonitoringIncidentRecord))
        events = list(await session.scalars(select(MonitoringIncidentEventRecord)))
        comparisons = await session.scalar(select(func.count()).select_from(DriftComparisonRecord))
    assert incident is not None
    assert incident.status == "open"
    assert incident.occurrence_count == 1
    assert [event.event_type for event in events] == ["incident_opened"]
    assert comparisons == 1


async def test_multiple_configurations_reuse_one_canonical_comparison() -> None:
    monitor_id, policy_id, first_configuration_id = await _seed_domain()
    await _snapshot(monitor_id, 0, 0.0)
    current_id = await _snapshot(monitor_id, 1, 0.2)
    async with SessionLocal() as session:
        second = await service.create_configuration(
            session,
            AutomaticDriftConfigurationCreate(
                name="second configuration",
                monitoring_definition_id=monitor_id,
                drift_policy_id=policy_id,
            ),
        )
    await _queue_and_run(first_configuration_id, current_id, START + timedelta(hours=3))
    await _queue_and_run(second.id, current_id, START + timedelta(hours=4))
    async with SessionLocal() as session:
        comparison_count = await session.scalar(
            select(func.count()).select_from(DriftComparisonRecord)
        )
        incident_count = await session.scalar(
            select(func.count()).select_from(MonitoringIncidentRecord)
        )
    assert comparison_count == 1
    assert incident_count == 2


async def test_no_drift_and_insufficient_evidence_do_not_open_incidents() -> None:
    monitor_id, _, configuration_id = await _seed_domain()
    await _snapshot(monitor_id, 0, 0.2)
    no_drift_id = await _snapshot(monitor_id, 1, 0.2)
    await _queue_and_run(configuration_id, no_drift_id, START + timedelta(hours=3))
    empty_id = await _snapshot(monitor_id, 2, None)
    await _queue_and_run(configuration_id, empty_id, START + timedelta(hours=4))
    async with SessionLocal() as session:
        incidents = await session.scalar(select(func.count()).select_from(MonitoringIncidentRecord))
        classifications = list(
            await session.scalars(
                select(DriftComparisonRecord.classification).order_by(
                    DriftComparisonRecord.created_at
                )
            )
        )
    assert incidents == 0
    assert classifications == ["no_drift_detected", "insufficient_evidence"]


async def test_acknowledged_incident_accumulates_drift_and_cooldown_only_suppresses_event() -> None:
    monitor_id, _, configuration_id = await _seed_domain(cooldown_seconds=3_600)
    await _snapshot(monitor_id, 0, 0.0)
    first_id = await _snapshot(monitor_id, 1, 0.2)
    await _queue_and_run(configuration_id, first_id, START + timedelta(hours=3))
    async with SessionLocal() as session:
        incident = await session.scalar(select(MonitoringIncidentRecord))
        assert incident is not None
        incident_id = incident.id
    async with SessionLocal() as session:
        acknowledged = await service.acknowledge_incident(
            session, incident_id, now=START + timedelta(hours=3, minutes=1)
        )
    assert acknowledged.status == "acknowledged"

    second_id = await _snapshot(monitor_id, 2, 0.4)
    suppressed_check = await _queue_and_run(
        configuration_id, second_id, START + timedelta(hours=3, minutes=10)
    )
    third_id = await _snapshot(monitor_id, 3, 0.6)
    await _queue_and_run(configuration_id, third_id, START + timedelta(hours=5))

    async with SessionLocal() as session:
        incident = await session.scalar(select(MonitoringIncidentRecord))
        check = await session.get(AutomaticDriftCheckRecord, suppressed_check)
        events = list(
            await session.scalars(
                select(MonitoringIncidentEventRecord).order_by(
                    MonitoringIncidentEventRecord.created_at
                )
            )
        )
    assert incident is not None and check is not None
    assert incident.status == "acknowledged"
    assert incident.occurrence_count == 3
    assert check.event_suppressed and check.event_suppression_reason == "cooldown"
    assert [event.event_type for event in events] == [
        "incident_opened",
        "incident_acknowledged",
        "drift_reoccurred",
    ]


async def test_consecutive_clean_resolution_drift_reset_and_reopening() -> None:
    monitor_id, _, configuration_id = await _seed_domain(cooldown_seconds=0, resolve_after=2)
    rates = [0.0, 0.2, 0.2, 0.4, 0.4, 0.4, 0.6]
    snapshot_ids = [await _snapshot(monitor_id, offset, rate) for offset, rate in enumerate(rates)]
    for offset, current_id in enumerate(snapshot_ids[1:6], start=1):
        await _queue_and_run(configuration_id, current_id, START + timedelta(hours=10 + offset))
    async with SessionLocal() as session:
        first = await session.scalar(
            select(MonitoringIncidentRecord).order_by(MonitoringIncidentRecord.created_at)
        )
        assert first is not None
        assert first.status == "resolved"
        assert first.occurrence_count == 2
        assert first.consecutive_clean_count == 2
        assert first.resolving_comparison_id is not None

    await _queue_and_run(configuration_id, snapshot_ids[6], START + timedelta(hours=20))
    async with SessionLocal() as session:
        incidents = list(
            await session.scalars(
                select(MonitoringIncidentRecord).order_by(MonitoringIncidentRecord.opened_at)
            )
        )
        event_types = list(
            await session.scalars(
                select(MonitoringIncidentEventRecord.event_type).order_by(
                    MonitoringIncidentEventRecord.created_at
                )
            )
        )
    assert [incident.status for incident in incidents] == ["resolved", "open"]
    assert event_types.count("incident_resolved") == 1
    assert event_types.count("incident_opened") == 2


async def test_phase8_end_to_end_preserves_canonical_comparisons_and_incident_history() -> None:
    monitor_id, _, configuration_id = await _seed_domain(cooldown_seconds=0, resolve_after=2)
    rates = [0.0, 0.2, 0.2, 0.4, 0.4, 0.4, 0.6]
    snapshot_ids = [await _snapshot(monitor_id, offset, rate) for offset, rate in enumerate(rates)]
    original = service.create_drift_comparison_in_transaction
    with patch.object(service, "create_drift_comparison_in_transaction", wraps=original) as reused:
        for offset, current_id in enumerate(snapshot_ids[1:], start=1):
            await _queue_and_run(configuration_id, current_id, START + timedelta(hours=10 + offset))

    async with SessionLocal() as session:
        incidents = list(
            await session.scalars(
                select(MonitoringIncidentRecord).order_by(MonitoringIncidentRecord.opened_at)
            )
        )
        events = list(
            await session.scalars(
                select(MonitoringIncidentEventRecord.event_type).order_by(
                    MonitoringIncidentEventRecord.created_at,
                    MonitoringIncidentEventRecord.id,
                )
            )
        )
        comparisons = list(
            await session.scalars(
                select(DriftComparisonRecord.classification).order_by(
                    DriftComparisonRecord.created_at,
                    DriftComparisonRecord.id,
                )
            )
        )
    assert reused.await_count == 6
    assert comparisons == [
        "drift_detected",
        "no_drift_detected",
        "drift_detected",
        "no_drift_detected",
        "no_drift_detected",
        "drift_detected",
    ]
    assert [(item.status, item.occurrence_count) for item in incidents] == [
        ("resolved", 2),
        ("open", 1),
    ]
    assert sum(item.status in {"open", "acknowledged"} for item in incidents) == 1
    assert events == [
        "incident_opened",
        "drift_reoccurred",
        "incident_resolved",
        "incident_opened",
    ]


def test_prometheus_metrics_are_bounded_and_match_persisted_phase8_state() -> None:
    async def seed() -> None:
        monitor_id, _, configuration_id = await _seed_domain()
        await _snapshot(monitor_id, 0, 0.0)
        current_id = await _snapshot(monitor_id, 1, 0.2)
        await _queue_and_run(configuration_id, current_id, START + timedelta(hours=3))

    asyncio.run(seed())
    with TestClient(create_app()) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE agentscope_monitoring_snapshots gauge" in response.text
    assert 'agentscope_monitoring_snapshots{status="completed"} 2' in response.text
    assert 'agentscope_automatic_drift_checks{status="completed"} 1' in response.text
    assert 'agentscope_monitoring_incidents{status="open"} 1' in response.text
    assert 'agentscope_drift_comparisons{classification="drift_detected"} 1' in response.text
    assert not any(
        forbidden in response.text.lower()
        for forbidden in ("uuid", "trace_id", "commit", "repository", "secret", "prompt")
    )


async def test_insufficient_does_not_advance_clean_count() -> None:
    monitor_id, _, configuration_id = await _seed_domain()
    first_id = await _snapshot(monitor_id, 0, 0.0)
    drift_id = await _snapshot(monitor_id, 1, 0.2)
    empty_id = await _snapshot(monitor_id, 2, None)
    await _queue_and_run(configuration_id, drift_id, START + timedelta(hours=5))
    await _queue_and_run(configuration_id, empty_id, START + timedelta(hours=6))
    async with SessionLocal() as session:
        incident = await session.scalar(select(MonitoringIncidentRecord))
    assert first_id != drift_id
    assert incident is not None
    assert incident.consecutive_clean_count == 0
    assert incident.latest_classification == "insufficient_evidence"


async def test_expired_lease_is_recovered_and_stale_owner_is_fenced() -> None:
    monitor_id, _, configuration_id = await _seed_domain()
    await _snapshot(monitor_id, 0, 0.0)
    current_id = await _snapshot(monitor_id, 1, 0.2)
    async with SessionLocal() as session, session.begin():
        check = AutomaticDriftCheckRecord(
            automatic_drift_configuration_id=configuration_id,
            current_snapshot_id=current_id,
            status="queued",
            queued_at=START,
        )
        session.add(check)
        await session.flush()
        check_id = check.id
    async with SessionLocal() as session:
        first = await service.claim_automatic_check(session, check_id, now=START)
    assert first is not None and first.attempt == 1
    async with SessionLocal() as session:
        assert await service.recover_automatic_checks(
            session, now=START + timedelta(minutes=3)
        ) == [check_id]
    async with SessionLocal() as session:
        second = await service.claim_automatic_check(
            session, check_id, now=START + timedelta(minutes=3)
        )
    assert second is not None and second.attempt == 2
    assert (
        await service.finalize_automatic_check(first, now=START + timedelta(minutes=3))
        is service.AutomaticDriftOutcome.IGNORED
    )
    assert (
        await service.finalize_automatic_check(second, now=START + timedelta(minutes=3, seconds=1))
        is service.AutomaticDriftOutcome.COMPLETED
    )


async def test_comparison_incident_and_event_rollback_together() -> None:
    monitor_id, _, configuration_id = await _seed_domain()
    await _snapshot(monitor_id, 0, 0.0)
    current_id = await _snapshot(monitor_id, 1, 0.2)
    async with SessionLocal() as session, session.begin():
        check = AutomaticDriftCheckRecord(
            automatic_drift_configuration_id=configuration_id,
            current_snapshot_id=current_id,
            status="queued",
            queued_at=START,
        )
        session.add(check)
        await session.flush()
        check_id = check.id
    async with SessionLocal() as session:
        claim = await service.claim_automatic_check(session, check_id, now=START)
    assert claim is not None
    with patch.object(service, "_apply_incident", side_effect=RuntimeError("forced rollback")):
        with pytest.raises(RuntimeError, match="forced rollback"):
            await service.finalize_automatic_check(claim, now=START + timedelta(seconds=1))
    assert (
        await service._handle_failure(claim, RuntimeError("forced rollback"))
        is service.AutomaticDriftOutcome.REQUEUED
    )
    async with SessionLocal() as session:
        counts = (
            int(await session.scalar(select(func.count()).select_from(DriftComparisonRecord)) or 0),
            int(
                await session.scalar(select(func.count()).select_from(MonitoringIncidentRecord))
                or 0
            ),
            int(
                await session.scalar(
                    select(func.count()).select_from(MonitoringIncidentEventRecord)
                )
                or 0
            ),
        )
    assert counts == (0, 0, 0)


async def test_comparison_failure_retries_are_bounded_and_monotonic() -> None:
    monitor_id, _, configuration_id = await _seed_domain()
    await _snapshot(monitor_id, 0, 0.0)
    current_id = await _snapshot(monitor_id, 1, 0.2)
    async with SessionLocal() as session, session.begin():
        check = AutomaticDriftCheckRecord(
            automatic_drift_configuration_id=configuration_id,
            current_snapshot_id=current_id,
            status="queued",
            queued_at=START,
        )
        session.add(check)
        await session.flush()
        check_id = check.id
    outcomes: list[service.AutomaticDriftOutcome] = []
    with patch.object(
        service, "create_drift_comparison_in_transaction", side_effect=RuntimeError("forced")
    ):
        for _ in range(4):
            outcomes.append(await service.process_automatic_drift_message(check_id))
    assert outcomes == [
        service.AutomaticDriftOutcome.REQUEUED,
        service.AutomaticDriftOutcome.REQUEUED,
        service.AutomaticDriftOutcome.REQUEUED,
        service.AutomaticDriftOutcome.FAILED,
    ]
    async with SessionLocal() as session:
        check = await session.get(AutomaticDriftCheckRecord, check_id)
    assert check is not None
    assert check.attempt_count == 4
    assert check.status == "failed"
    assert check.failure_reason == "comparison_failure"


async def test_broker_failure_releases_reservation_for_periodic_recovery() -> None:
    monitor_id, _, _ = await _seed_domain()
    await _snapshot(monitor_id, 0, 0.0)

    def unavailable(_: UUID) -> None:
        raise ConnectionError("redis unavailable")

    recovery_time = START + timedelta(days=1)
    assert await recover_automatic_drift_once(enqueue=unavailable, now=recovery_time) == 0
    sent: list[UUID] = []
    assert await recover_automatic_drift_once(enqueue=sent.append, now=recovery_time) == 1
    assert len(sent) == 1


def test_configuration_check_incident_and_event_apis() -> None:
    async def seed() -> tuple[UUID, UUID, UUID, UUID, UUID]:
        monitor_id, policy_id, configuration_id = await _seed_domain()
        await _snapshot(monitor_id, 0, 0.0)
        current_id = await _snapshot(monitor_id, 1, 0.2)
        await _queue_and_run(configuration_id, current_id, START + timedelta(hours=3))
        async with SessionLocal() as session:
            incident = await session.scalar(select(MonitoringIncidentRecord))
            check = await session.scalar(select(AutomaticDriftCheckRecord))
            assert incident is not None and check is not None
            return incident.id, check.id, configuration_id, monitor_id, policy_id

    incident_id, check_id, configuration_id, monitor_id, policy_id = asyncio.run(seed())
    assert incident_id is not None and check_id is not None and configuration_id is not None
    with TestClient(create_app()) as client:
        created = client.post(
            "/api/v1/automatic-drift-configurations",
            json={
                "name": "second configuration",
                "monitoring_definition_id": str(monitor_id),
                "drift_policy_id": str(policy_id),
                "cooldown_seconds": 0,
                "resolve_after_clean_windows": 3,
            },
        )
        configuration = client.get(f"/api/v1/automatic-drift-configurations/{configuration_id}")
        disabled = client.patch(
            f"/api/v1/automatic-drift-configurations/{configuration_id}",
            json={"is_enabled": False},
        )
        checks = client.get("/api/v1/automatic-drift-checks", params={"page_size": 1})
        check = client.get(f"/api/v1/automatic-drift-checks/{check_id}")
        incidents = client.get("/api/v1/monitoring-incidents", params={"status": "open"})
        acknowledged = client.post(f"/api/v1/monitoring-incidents/{incident_id}/acknowledge")
        incident = client.get(f"/api/v1/monitoring-incidents/{incident_id}")
        events = client.get(f"/api/v1/monitoring-incidents/{incident_id}/events")
        invalid = client.get("/api/v1/monitoring-incidents", params={"page_size": 101})
    assert created.status_code == 201, created.text
    assert configuration.status_code == disabled.status_code == 200
    assert disabled.json()["is_enabled"] is False
    assert checks.json()["items"][0]["id"] == str(check_id)
    assert check.json()["id"] == str(check_id)
    assert incidents.json()["items"][0]["id"] == str(incident_id)
    assert acknowledged.json()["status"] == incident.json()["status"] == "acknowledged"
    assert [event["event_type"] for event in events.json()["items"]] == [
        "incident_opened",
        "incident_acknowledged",
    ]
    assert invalid.status_code == 422
