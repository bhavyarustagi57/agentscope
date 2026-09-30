from __future__ import annotations

import importlib.util
import subprocess
import sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from agentscope_api.database import SessionLocal
from agentscope_api.main import create_app
from agentscope_api.models.automatic_drift import (
    MonitoringIncidentEventRecord,
    MonitoringIncidentRecord,
)
from agentscope_api.models.calibration import HumanAnnotationRecord
from agentscope_api.models.drift import DriftComparisonRecord
from agentscope_api.models.evaluation import EvaluationResultRecord
from agentscope_api.models.experiment import (
    ExperimentConditionAnalysisRecord,
    ExperimentRunAnalysisRecord,
)
from agentscope_api.models.judging import CalibrationAnalysisRecord
from agentscope_api.models.monitoring import MonitoringSnapshotRecord
from agentscope_api.models.regression import RegressionCheckRecord
from agentscope_api.models.trace import TraceRecord

pytestmark = pytest.mark.usefixtures("clean_database")


def _script_module(name: str):
    script = Path(__file__).parents[1] / "scripts" / name
    sys.path.insert(0, str(script.parent))
    spec = importlib.util.spec_from_file_location("agentscope_demo_seed", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _TestApi:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(self, method: str, path: str, payload=None):
        response = self.client.request(method, path, json=payload)
        assert response.status_code < 400, response.text
        return response.json() if response.content else None


def test_demo_seed_is_complete_local_and_idempotent() -> None:
    demo = _script_module("seed_demo.py")

    with TestClient(create_app()) as client:
        assert client.portal is not None
        non_demo = deepcopy(demo._traces()[0])
        original_trace_id = non_demo["trace_id"]
        non_demo["trace_id"] = "existing-user-trace"
        non_demo["name"] = "Existing user evidence"
        non_demo["metadata"] = {"owner": "user"}
        non_demo["tags"] = ["production"]
        for span in non_demo["spans"]:
            span["trace_id"] = non_demo["trace_id"]
            span["span_id"] = span["span_id"].replace(original_trace_id, non_demo["trace_id"], 1)
            if span["parent_span_id"]:
                span["parent_span_id"] = span["parent_span_id"].replace(
                    original_trace_id, non_demo["trace_id"], 1
                )
            span["metadata"] = {"owner": "user"}
        response = client.post("/api/v1/traces", json={"schema_version": "1", "traces": [non_demo]})
        assert response.status_code == 202
        first = demo.seed_demo(_TestApi(client), client.portal.call)
        second = demo.seed_demo(_TestApi(client), client.portal.call)

    assert first["trace_ids"] == second["trace_ids"]
    assert first["ids"] == second["ids"]
    assert first["routes"] == second["routes"]
    assert first["routes"]["representative_trace"] == (
        "/traces/agentscope-demo-workspace-v1-00"
    )
    assert first["routes"]["calibration_report"].endswith(first["ids"]["judge_run"])
    assert first["routes"]["incident"].endswith(first["ids"]["monitoring_incident"])
    assert first["paid_provider_calls"] == second["paid_provider_calls"] == 0
    assert first["created"] > 0
    assert second["created"] == 0

    async def verify() -> tuple[int, ...]:
        async with SessionLocal() as session:
            traces = list(await session.scalars(select(TraceRecord)))
            demo_traces = [trace for trace in traces if trace.metadata_.get("agentscope_demo")]
            assert len(demo_traces) == 20
            assert {trace.status for trace in demo_traces} == {"success", "error"}
            assert any(trace.trace_id == "existing-user-trace" for trace in traces)

            calibration = await session.scalar(select(CalibrationAnalysisRecord))
            assert calibration is not None
            assert (calibration.false_positive, calibration.false_negative) == (1, 1)
            assert calibration.cohens_kappa == pytest.approx(0.0)

            condition = await session.scalar(select(ExperimentConditionAnalysisRecord))
            assert condition is not None
            assert condition.sample_size == 5
            assert condition.pass_rate_difference == pytest.approx(-0.2)
            assert condition.a_only_passed_count == 2
            assert condition.b_only_passed_count == 1

            regression = await session.scalar(select(RegressionCheckRecord))
            assert regression is not None
            assert regression.classification == "regression_detected"

            snapshots = list(
                await session.scalars(
                    select(MonitoringSnapshotRecord).order_by(MonitoringSnapshotRecord.window_start)
                )
            )
            assert [snapshot.window_start for snapshot in snapshots] == [
                demo.START + timedelta(hours=offset) for offset in range(7)
            ]

            incidents = list(await session.scalars(select(MonitoringIncidentRecord)))
            assert {incident.status for incident in incidents} == {"open", "resolved"}

            return (
                int(await session.scalar(select(func.count()).select_from(TraceRecord)) or 0),
                int(
                    await session.scalar(select(func.count()).select_from(EvaluationResultRecord))
                    or 0
                ),
                int(
                    await session.scalar(select(func.count()).select_from(HumanAnnotationRecord))
                    or 0
                ),
                int(
                    await session.scalar(
                        select(func.count()).select_from(CalibrationAnalysisRecord)
                    )
                    or 0
                ),
                int(
                    await session.scalar(
                        select(func.count()).select_from(ExperimentRunAnalysisRecord)
                    )
                    or 0
                ),
                int(
                    await session.scalar(select(func.count()).select_from(RegressionCheckRecord))
                    or 0
                ),
                int(
                    await session.scalar(select(func.count()).select_from(MonitoringSnapshotRecord))
                    or 0
                ),
                int(
                    await session.scalar(select(func.count()).select_from(DriftComparisonRecord))
                    or 0
                ),
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

    counts = __import__("asyncio").run(verify())
    assert counts[0] == 21
    assert counts[1] == 20
    assert counts[2] >= 4
    assert counts[3:6] == (1, 1, 1)
    assert counts[6:] == (7, 6, 2, 4)


def test_demo_seed_recovers_from_an_incomplete_calibration_run(monkeypatch) -> None:
    demo = _script_module("seed_demo.py")
    process_judge_message = demo.process_judge_message

    async def interrupt_judge(*_: object) -> None:
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(demo, "process_judge_message", interrupt_judge)
    with TestClient(create_app()) as client:
        assert client.portal is not None
        with pytest.raises(RuntimeError, match="simulated interruption"):
            demo.seed_demo(_TestApi(client), client.portal.call)
        monkeypatch.setattr(demo, "process_judge_message", process_judge_message)
        result = demo.seed_demo(_TestApi(client), client.portal.call)

    assert result["ids"]["judge_run"]
    assert result["paid_provider_calls"] == 0


def test_demo_seed_recovers_from_an_incomplete_experiment_run(monkeypatch) -> None:
    demo = _script_module("seed_demo.py")
    process_experiment_message = demo.process_experiment_message

    async def interrupt_experiment(*_: object) -> None:
        raise RuntimeError("simulated experiment interruption")

    monkeypatch.setattr(demo, "process_experiment_message", interrupt_experiment)
    with TestClient(create_app()) as client:
        assert client.portal is not None
        with pytest.raises(RuntimeError, match="simulated experiment interruption"):
            demo.seed_demo(_TestApi(client), client.portal.call)
        monkeypatch.setattr(demo, "process_experiment_message", process_experiment_message)
        result = demo.seed_demo(_TestApi(client), client.portal.call)

    assert result["ids"]["experiment_run"]
    assert result["paid_provider_calls"] == 0


def test_bisection_demo_stays_in_owned_workspace_and_finds_boundary(tmp_path: Path) -> None:
    demo = _script_module("run_bisection_demo.py")
    unowned = tmp_path / "unowned"
    unowned.mkdir()
    with pytest.raises(RuntimeError, match="refusing non-demo workspace"):
        demo._owned_repository(unowned)
    repository = Path(__file__).parents[3]
    before = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        shell=False,
    ).stdout
    with TestClient(create_app()) as client:
        assert client.portal is not None
        result = demo.run_bisection_demo(
            _TestApi(client), tmp_path / "owned-demo", client.portal.call
        )
    after = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        shell=False,
    ).stdout
    assert result["target_unchanged"] is True
    assert result["previous_good"] != result["first_regressed"]
    assert str((tmp_path / "owned-demo").resolve()) in result["repository"]
    assert before == after


def test_demo_adds_no_schema_migration() -> None:
    versions = Path(__file__).parents[1] / "alembic" / "versions"
    migration_names = {path.name for path in versions.glob("*.py")}
    assert any("0018" in name for name in migration_names)
    assert not any("0019" in name for name in migration_names)
