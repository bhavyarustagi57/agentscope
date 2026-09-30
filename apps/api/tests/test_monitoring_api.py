from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agentscope_api.database import SessionLocal
from agentscope_api.main import create_app
from agentscope_api.models.monitoring import MonitoringSnapshotRecord

pytestmark = pytest.mark.usefixtures("clean_database")


def test_definition_create_list_get_toggle_and_validation() -> None:
    with TestClient(create_app()) as client:
        created = client.post(
            "/api/v1/monitoring-definitions",
            json={
                "name": "Checkout reliability",
                "description": "Closed production windows",
                "window_duration": "15m",
                "trace_scope": {"trace_name": "checkout", "trace_status": "error"},
            },
        )
        assert created.status_code == 201, created.text
        monitor = created.json()
        detail = client.get(f"/api/v1/monitoring-definitions/{monitor['id']}")
        listing = client.get(
            "/api/v1/monitoring-definitions", params={"is_enabled": True, "page_size": 1}
        )
        disabled = client.patch(
            f"/api/v1/monitoring-definitions/{monitor['id']}", json={"is_enabled": False}
        )
        invalid_duration = client.post(
            "/api/v1/monitoring-definitions",
            json={"name": "bad", "window_duration": "10m"},
        )
        missing_evaluation = client.post(
            "/api/v1/monitoring-definitions",
            json={
                "name": "bad link",
                "window_duration": "1h",
                "evaluation_definition_id": "00000000-0000-0000-0000-000000000000",
            },
        )

    assert detail.status_code == listing.status_code == disabled.status_code == 200
    assert detail.json()["trace_scope"] == {
        "trace_name": "checkout",
        "trace_status": "error",
    }
    assert listing.json()["items"][0]["id"] == monitor["id"]
    assert disabled.json()["is_enabled"] is False
    assert invalid_duration.status_code == 422
    assert missing_evaluation.status_code == 404
    assert missing_evaluation.json()["error"]["code"] == "EVALUATION_DEFINITION_NOT_FOUND"


def test_materialization_commits_intent_before_broker_and_is_idempotent() -> None:
    observed: list[tuple[str, str]] = []

    def inspect(snapshot_id: object) -> None:
        with TestClient(create_app()) as nested:
            response = nested.get(f"/api/v1/monitoring-snapshots/{snapshot_id}")
        observed.append((str(snapshot_id), response.json()["status"]))

    with TestClient(create_app()) as client:
        monitor = client.post(
            "/api/v1/monitoring-definitions",
            json={"name": "Hourly", "window_duration": "1h"},
        ).json()
        payload = {
            "window_start": "2020-09-25T10:00:00Z",
            "window_end": "2020-09-25T11:00:00Z",
        }
        with patch("agentscope_api.api.routes.monitoring.enqueue_monitoring_snapshot", inspect):
            first = client.post(
                f"/api/v1/monitoring-definitions/{monitor['id']}/snapshots", json=payload
            )
        duplicate = client.post(
            f"/api/v1/monitoring-definitions/{monitor['id']}/snapshots", json=payload
        )

    assert first.status_code == duplicate.status_code == 202
    assert first.json()["created"] is True
    assert duplicate.json()["created"] is False
    assert first.json()["snapshot_id"] == duplicate.json()["snapshot_id"]
    assert observed == [(first.json()["snapshot_id"], "queued")]


def test_open_future_or_misaligned_window_is_rejected() -> None:
    with TestClient(create_app()) as client:
        monitor = client.post(
            "/api/v1/monitoring-definitions",
            json={"name": "Hourly", "window_duration": "1h"},
        ).json()
        misaligned = client.post(
            f"/api/v1/monitoring-definitions/{monitor['id']}/snapshots",
            json={
                "window_start": "2020-09-25T10:01:00Z",
                "window_end": "2020-09-25T11:01:00Z",
            },
        )
        future = client.post(
            f"/api/v1/monitoring-definitions/{monitor['id']}/snapshots",
            json={
                "window_start": "2099-01-01T00:00:00Z",
                "window_end": "2099-01-01T01:00:00Z",
            },
        )

    assert misaligned.status_code == future.status_code == 422


def test_broker_failure_leaves_queued_intent_and_list_filters_are_bounded() -> None:
    with TestClient(create_app()) as client:
        monitor = client.post(
            "/api/v1/monitoring-definitions",
            json={"name": "Hourly", "window_duration": "1h"},
        ).json()
        with patch(
            "agentscope_api.api.routes.monitoring.enqueue_monitoring_snapshot",
            side_effect=ConnectionError("redis unavailable"),
        ):
            response = client.post(
                f"/api/v1/monitoring-definitions/{monitor['id']}/snapshots",
                json={
                    "window_start": "2020-09-25T10:00:00Z",
                    "window_end": "2020-09-25T11:00:00Z",
                },
            )
        listing = client.get(
            f"/api/v1/monitoring-definitions/{monitor['id']}/snapshots",
            params={"status": "queued", "page_size": 1},
        )
        malformed = client.get(
            f"/api/v1/monitoring-definitions/{monitor['id']}/snapshots",
            params={"window_start_gte": "2020-09-25T10:00:00"},
        )

    assert response.status_code == 202
    assert response.json()["queue_delivery"] == "deferred"
    assert listing.json()["items"][0]["id"] == response.json()["snapshot_id"]
    assert malformed.status_code == 422


def test_database_rejects_duplicate_canonical_window() -> None:
    with TestClient(create_app()) as client:
        monitor = client.post(
            "/api/v1/monitoring-definitions",
            json={"name": "Hourly", "window_duration": "1h"},
        ).json()
        created = client.post(
            f"/api/v1/monitoring-definitions/{monitor['id']}/snapshots",
            json={
                "window_start": "2020-09-25T10:00:00Z",
                "window_end": "2020-09-25T11:00:00Z",
            },
        ).json()

    async def duplicate() -> None:
        async with SessionLocal() as session, session.begin():
            original = await session.get(MonitoringSnapshotRecord, UUID(created["snapshot_id"]))
            assert original is not None
            session.add(
                MonitoringSnapshotRecord(
                    monitoring_definition_id=original.monitoring_definition_id,
                    window_start=original.window_start,
                    window_end=original.window_end,
                    status="queued",
                    queued_at=original.queued_at,
                )
            )

    import asyncio

    with pytest.raises(IntegrityError):
        asyncio.run(duplicate())


def test_successful_delivery_survives_enqueue_marker_failure() -> None:
    with TestClient(create_app()) as client:
        monitor = client.post(
            "/api/v1/monitoring-definitions",
            json={"name": "Hourly", "window_duration": "1h"},
        ).json()
        with (
            patch("agentscope_api.api.routes.monitoring.enqueue_monitoring_snapshot"),
            patch(
                "agentscope_api.api.routes.monitoring.mark_snapshot_enqueued",
                new=AsyncMock(side_effect=SQLAlchemyError("write failed")),
            ),
        ):
            response = client.post(
                f"/api/v1/monitoring-definitions/{monitor['id']}/snapshots",
                json={
                    "window_start": "2020-09-25T10:00:00Z",
                    "window_end": "2020-09-25T11:00:00Z",
                },
            )

    assert response.status_code == 202
    assert response.json()["queue_delivery"] == "enqueued"
