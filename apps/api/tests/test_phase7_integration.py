from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_bisection_analysis import _create_and_start_analysis, _drive_analysis, _probe
from test_bisections import _create_session, _git, _repository
from test_experiment_analysis import _completed_run
from test_regressions import _create_check, _policy

from agentscope_api.core.config import get_settings
from agentscope_api.database import SessionLocal
from agentscope_api.main import create_app
from agentscope_api.models.experiment import ExperimentRunRecord, ExperimentVariantRecord


@pytest.mark.usefixtures("clean_database")
def test_phase6_evidence_drives_regression_and_attributed_bisection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, commits = _repository(tmp_path, commit_count=6)
    monkeypatch.setenv("BISECTION_WORK_ROOT", str(tmp_path / "phase7-worktrees"))
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            run = _completed_run(
                client,
                [[("passed", "passed")] * 3 + [("passed", "failed")]],
            )
            phase6 = client.post(f"/api/v1/experiment-runs/{run['id']}/analysis")
            assert phase6.status_code == 201, phase6.text
            assert phase6.json()["conditions"][0]["result"]["pass_rate_difference"] == -0.25

            async def add_git_provenance() -> None:
                async with SessionLocal() as database, database.begin():
                    run_record = await database.get(ExperimentRunRecord, UUID(str(run["id"])))
                    assert run_record is not None
                    variants = list(
                        await database.scalars(
                            select(ExperimentVariantRecord).where(
                                ExperimentVariantRecord.experiment_id == run_record.experiment_id
                            )
                        )
                    )
                    for variant in variants:
                        variant.provenance = {
                            "git_commit_sha": commits[0 if variant.variant_key == "A" else -1]
                        }

            asyncio.run(add_git_provenance())
            policy = _policy(client, drop=0.25, minimum_sample_size=4)
            check_response = _create_check(client, run["id"], policy["id"])
            assert check_response.status_code == 201, check_response.text
            check = check_response.json()
            assert check["classification"] == "regression_detected"
            assert check["baseline_provenance"]["git_commit_sha"] == commits[0]
            assert check["candidate_provenance"]["git_commit_sha"] == commits[-1]

            session_response = _create_session(client, check["id"], repository)
            assert session_response.status_code == 201, session_response.text
            session = session_response.json()
            before = (
                _git(repository, "rev-parse", "HEAD"),
                _git(repository, "branch", "--show-current"),
                _git(repository, "status", "--porcelain=v1"),
            )

            analysis = _create_and_start_analysis(client, session["id"], _probe(3))
            attributed = _drive_analysis(client, analysis["id"])
            execution_runs = client.get(
                f"/api/v1/bisection-sessions/{session['id']}/execution-runs"
            )

        assert attributed["status"] == "attributed"
        assert attributed["final_good_commit_sha"] == commits[2]
        assert attributed["final_bad_commit_sha"] == commits[3]
        assert {step["observed_outcome"] for step in attributed["steps"]} == {
            "pass",
            "regression",
        }
        assert execution_runs.status_code == 200
        assert execution_runs.json()["items"]
        assert before == (
            _git(repository, "rev-parse", "HEAD"),
            _git(repository, "branch", "--show-current"),
            _git(repository, "status", "--porcelain=v1"),
        )
    finally:
        get_settings.cache_clear()
