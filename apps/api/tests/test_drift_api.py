from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from agentscope_api.database import SessionLocal
from agentscope_api.main import create_app
from agentscope_api.models.monitoring import MonitoringDefinitionRecord, MonitoringSnapshotRecord

START = datetime(2026, 9, 25, 10, tzinfo=UTC)


def _policy(
    client: TestClient, *, rules: list[dict[str, object]] | None = None
) -> dict[str, object]:
    response = client.post(
        "/api/v1/drift-policies",
        json={
            "name": "Production drift guard",
            "description": "Practical changes only",
            "rules": rules
            or [
                {
                    "metric": "trace_failure_rate",
                    "direction": "increase",
                    "threshold_type": "absolute",
                    "practical_threshold": 0.1,
                    "minimum_baseline_samples": 10,
                    "minimum_current_samples": 10,
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _seed_pair(
    *,
    different_monitors: bool = False,
    different_duration: bool = False,
    overlap: bool = False,
    incomplete: bool = False,
    baseline_after_current: bool = False,
    baseline_values: dict[str, object] | None = None,
    current_values: dict[str, object] | None = None,
) -> tuple[UUID, UUID]:
    async with SessionLocal() as session, session.begin():
        baseline_monitor = MonitoringDefinitionRecord(
            name="hourly checkout",
            description=None,
            is_enabled=True,
            window_duration_seconds=3_600,
            trace_name=None,
            trace_status=None,
            evaluation_definition_id=None,
        )
        session.add(baseline_monitor)
        await session.flush()
        current_monitor = baseline_monitor
        if different_monitors:
            current_monitor = MonitoringDefinitionRecord(
                name="other hourly monitor",
                description=None,
                is_enabled=True,
                window_duration_seconds=3_600,
                trace_name=None,
                trace_status=None,
                evaluation_definition_id=None,
            )
            session.add(current_monitor)
            await session.flush()

        baseline_start = START + timedelta(hours=2) if baseline_after_current else START
        baseline_end = baseline_start + timedelta(hours=1)
        current_start = (
            START
            if baseline_after_current
            else START + (timedelta(minutes=30) if overlap else timedelta(hours=1))
        )
        current_end = current_start + timedelta(hours=2 if different_duration else 1)
        defaults: dict[str, object] = {
            "status": "completed",
            "trace_count": 100,
            "successful_trace_count": 90,
            "failed_trace_count": 10,
            "success_rate": 0.9,
            "failure_rate": 0.1,
            "duration_sample_count": 100,
            "mean_duration_ms": 100.0,
            "median_duration_ms": 90.0,
            "p95_duration_ms": 200.0,
            "token_sample_count": 100,
            "input_tokens": 2_000,
            "output_tokens": 3_000,
            "total_tokens": 5_000,
            "mean_total_tokens": 50.0,
            "evaluated_result_count": 100,
            "passed_evaluation_count": 80,
            "failed_evaluation_count": 10,
            "evaluator_error_count": 10,
            "valid_binary_evaluation_count": 90,
            "evaluation_pass_rate": 80 / 90,
            "evaluation_error_rate": 0.1,
            "attempt_count": 1,
            "completed_at": START + timedelta(hours=4),
        }
        baseline_data = defaults | (baseline_values or {})
        current_data = defaults | (current_values or {})
        if incomplete:
            current_data |= {"status": "queued", "completed_at": None, "attempt_count": 0}
        baseline = MonitoringSnapshotRecord(
            monitoring_definition_id=baseline_monitor.id,
            window_start=baseline_start,
            window_end=baseline_end,
            **baseline_data,
        )
        current = MonitoringSnapshotRecord(
            monitoring_definition_id=current_monitor.id,
            window_start=current_start,
            window_end=current_end,
            **current_data,
        )
        session.add_all([baseline, current])
        await session.flush()
        return baseline.id, current.id


def _compare(client: TestClient, policy_id: object, baseline_id: UUID, current_id: UUID):
    return client.post(
        "/api/v1/drift-comparisons",
        json={
            "drift_policy_id": policy_id,
            "baseline_snapshot_id": str(baseline_id),
            "current_snapshot_id": str(current_id),
        },
    )


def test_drift_routes_are_registered() -> None:
    paths = create_app().openapi()["paths"]

    assert "/api/v1/drift-policies" in paths
    assert "/api/v1/drift-comparisons" in paths


@pytest.mark.usefixtures("clean_database")
def test_policy_create_list_get_preserves_ordered_rules() -> None:
    rules = [
        {
            "metric": "mean_duration_ms",
            "direction": "increase",
            "threshold_type": "relative",
            "practical_threshold": 0.25,
            "minimum_baseline_samples": 20,
            "minimum_current_samples": 30,
        },
        {
            "metric": "evaluation_pass_rate",
            "direction": "decrease",
            "threshold_type": "absolute",
            "practical_threshold": 0.05,
            "minimum_baseline_samples": 10,
            "minimum_current_samples": 10,
        },
    ]
    with TestClient(create_app()) as client:
        created = _policy(client, rules=rules)
        listing = client.get("/api/v1/drift-policies", params={"page_size": 1})
        fetched = client.get(f"/api/v1/drift-policies/{created['id']}")

    assert listing.status_code == fetched.status_code == 200
    assert fetched.json() == created
    assert [item["position"] for item in created["rules"]] == [0, 1]
    assert listing.json() == {"items": [created], "has_more": False}


@pytest.mark.usefixtures("clean_database")
def test_comparison_persists_ordered_findings_and_conservative_overall_status() -> None:
    baseline_id, current_id = asyncio.run(
        _seed_pair(
            current_values={
                "failed_trace_count": 25,
                "successful_trace_count": 75,
                "failure_rate": 0.25,
                "success_rate": 0.75,
                "mean_duration_ms": 130.0,
                "token_sample_count": 0,
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "mean_total_tokens": None,
            }
        )
    )
    rules = [
        {
            "metric": "trace_failure_rate",
            "direction": "increase",
            "threshold_type": "absolute",
            "practical_threshold": 0.1,
            "minimum_baseline_samples": 10,
            "minimum_current_samples": 10,
        },
        {
            "metric": "mean_duration_ms",
            "direction": "increase",
            "threshold_type": "relative",
            "practical_threshold": 0.25,
            "minimum_baseline_samples": 10,
            "minimum_current_samples": 10,
        },
        {
            "metric": "mean_total_tokens",
            "direction": "increase",
            "threshold_type": "relative",
            "practical_threshold": 0.1,
            "minimum_baseline_samples": 10,
            "minimum_current_samples": 10,
        },
    ]
    with TestClient(create_app()) as client:
        policy = _policy(client, rules=rules)
        created = _compare(client, policy["id"], baseline_id, current_id)
        fetched = client.get(f"/api/v1/drift-comparisons/{created.json()['id']}")

    assert created.status_code == 201, created.text
    assert fetched.json() == created.json()
    assert created.json()["classification"] == "drift_detected"
    findings = created.json()["findings"]
    assert [item["rule_position"] for item in findings] == [0, 1, 2]
    assert [item["classification"] for item in findings] == [
        "drift_detected",
        "drift_detected",
        "insufficient_evidence",
    ]
    assert findings[0]["p_value"] is not None
    assert findings[1]["p_value"] is None


@pytest.mark.usefixtures("clean_database")
@pytest.mark.parametrize(
    ("seed_options", "same_snapshot", "expected_code"),
    [
        ({}, True, "DRIFT_SNAPSHOTS_MUST_DIFFER"),
        ({"different_monitors": True}, False, "DRIFT_MONITOR_MISMATCH"),
        ({"different_duration": True}, False, "DRIFT_WINDOW_DURATION_MISMATCH"),
        ({"overlap": True}, False, "DRIFT_WINDOWS_OVERLAP"),
        ({"incomplete": True}, False, "DRIFT_SNAPSHOT_NOT_COMPLETED"),
        ({"baseline_after_current": True}, False, "DRIFT_BASELINE_NOT_BEFORE_CURRENT"),
    ],
)
def test_invalid_snapshot_pairs_are_rejected_without_partial_comparison(
    seed_options: dict[str, bool], same_snapshot: bool, expected_code: str
) -> None:
    baseline_id, current_id = asyncio.run(_seed_pair(**seed_options))
    if same_snapshot:
        current_id = baseline_id
    with TestClient(create_app()) as client:
        policy = _policy(client)
        response = _compare(client, policy["id"], baseline_id, current_id)
        listing = client.get("/api/v1/drift-comparisons")

    assert response.status_code in {409, 422}
    assert response.json()["error"]["code"] == expected_code
    assert listing.json()["items"] == []


@pytest.mark.usefixtures("clean_database")
def test_policy_and_snapshot_changes_do_not_rewrite_comparison() -> None:
    baseline_id, current_id = asyncio.run(_seed_pair())
    with TestClient(create_app()) as client:
        policy = _policy(client)
        created = _compare(client, policy["id"], baseline_id, current_id).json()

        async def mutate_sources() -> None:
            from agentscope_api.models.drift import DriftPolicyRecord, DriftPolicyRuleRecord

            async with SessionLocal() as session, session.begin():
                policy_record = await session.get(DriftPolicyRecord, UUID(str(policy["id"])))
                rule = await session.get(DriftPolicyRuleRecord, (UUID(str(policy["id"])), 0))
                current = await session.get(MonitoringSnapshotRecord, current_id)
                assert policy_record is not None and rule is not None and current is not None
                policy_record.name = "Changed later"
                rule.practical_threshold = 0.9
                current.failure_rate = 0.9
                current.failed_trace_count = 90
                current.success_rate = 0.1
                current.successful_trace_count = 10

        asyncio.run(mutate_sources())
        fetched = client.get(f"/api/v1/drift-comparisons/{created['id']}")

    assert fetched.json() == created


@pytest.mark.usefixtures("clean_database")
def test_list_filters_and_pagination_are_bounded() -> None:
    baseline_id, current_id = asyncio.run(_seed_pair())
    with TestClient(create_app()) as client:
        policy = _policy(client)
        created = _compare(client, policy["id"], baseline_id, current_id).json()
        filtered = client.get(
            "/api/v1/drift-comparisons",
            params={
                "monitoring_definition_id": created["monitoring_definition_id"],
                "drift_policy_id": policy["id"],
                "current_snapshot_id": str(current_id),
                "classification": created["classification"],
                "page_size": 1,
            },
        )
        invalid = client.get("/api/v1/drift-comparisons", params={"page_size": 101})

    assert [item["id"] for item in filtered.json()["items"]] == [created["id"]]
    assert invalid.status_code == 422


@pytest.mark.usefixtures("clean_database")
def test_policy_or_snapshot_not_found_is_explicit() -> None:
    baseline_id, current_id = asyncio.run(_seed_pair())
    missing = "00000000-0000-0000-0000-000000000000"
    with TestClient(create_app()) as client:
        missing_policy = _compare(client, missing, baseline_id, current_id)
        policy = _policy(client)
        missing_snapshot = _compare(client, policy["id"], UUID(missing), current_id)

    assert missing_policy.status_code == missing_snapshot.status_code == 404
    assert missing_policy.json()["error"]["code"] == "DRIFT_POLICY_NOT_FOUND"
    assert missing_snapshot.json()["error"]["code"] == "MONITORING_SNAPSHOT_NOT_FOUND"


@pytest.mark.usefixtures("clean_database")
def test_comparison_and_findings_rollback_together() -> None:
    baseline_id, current_id = asyncio.run(_seed_pair())
    with TestClient(create_app()) as client:
        policy = _policy(
            client,
            rules=[
                {
                    "metric": metric,
                    "direction": "increase",
                    "threshold_type": "absolute",
                    "practical_threshold": 0.01,
                    "minimum_baseline_samples": 1,
                    "minimum_current_samples": 1,
                }
                for metric in ("trace_failure_rate", "trace_success_rate")
            ],
        )

        from agentscope_api.services import drift as drift_service

        original = drift_service.evaluate_rule
        calls = 0

        def fail_second(*args: object, **kwargs: object):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("forced finding failure")
            return original(*args, **kwargs)

        async def fail_atomically() -> None:
            async with SessionLocal() as session:
                await drift_service.create_drift_comparison(
                    session,
                    drift_service.DriftComparisonCreate(
                        drift_policy_id=UUID(str(policy["id"])),
                        baseline_snapshot_id=baseline_id,
                        current_snapshot_id=current_id,
                    ),
                )

        with patch("agentscope_api.services.drift.evaluate_rule", side_effect=fail_second):
            with pytest.raises(RuntimeError, match="forced finding failure"):
                asyncio.run(fail_atomically())

    async def count_rows() -> tuple[int, int]:
        from agentscope_api.models.drift import DriftComparisonRecord, DriftFindingRecord

        async with SessionLocal() as session:
            comparisons = await session.scalar(
                select(func.count()).select_from(DriftComparisonRecord)
            )
            findings = await session.scalar(select(func.count()).select_from(DriftFindingRecord))
            return int(comparisons or 0), int(findings or 0)

    assert asyncio.run(count_rows()) == (0, 0)
