from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from test_bisection_execution import _planned_session
from test_bisections import _create_session, _regression_check, _repository

from agentscope_api.core.config import get_settings
from agentscope_api.database import SessionLocal
from agentscope_api.jobs.recover import recover_bisection_analyses_once
from agentscope_api.main import create_app
from agentscope_api.models.bisection_analysis import BisectionAnalysisRecord
from agentscope_api.services.bisection_analysis import (
    contradictory_evidence,
    select_position,
    updated_boundaries,
)
from agentscope_api.services.bisection_execution import (
    ProcessOutcome,
    process_execution_message,
)
from agentscope_api.services.bisection_orchestration import (
    AnalysisProcessOutcome,
    advance_analysis_claim,
    claim_analysis,
    process_analysis_message,
    recover_analyses,
)


@pytest.mark.parametrize(
    ("good_position", "bad_position", "expected"),
    [
        (-1, 5, 2),
        (-1, 4, 1),
        (0, 5, 2),
        (2, 3, None),
    ],
)
def test_midpoint_selection_is_deterministic(
    good_position: int, bad_position: int, expected: int | None
) -> None:
    assert select_position(good_position, bad_position, set()) == expected


def test_unusable_midpoint_selects_nearest_unobserved_with_lower_tie_break() -> None:
    assert select_position(-1, 5, {2}) == 1
    assert select_position(-1, 5, {1, 2}) == 3
    assert select_position(-1, 5, {0, 1, 2, 3, 4}) is None


def test_pass_and_regression_move_only_the_corresponding_boundary() -> None:
    assert updated_boundaries(-1, 5, 2, "pass") == (2, 5)
    assert updated_boundaries(-1, 5, 2, "regression") == (-1, 2)
    assert updated_boundaries(-1, 5, 2, "indeterminate") == (-1, 5)
    assert updated_boundaries(-1, 5, 2, "execution_failed") == (-1, 5)


def test_conflicting_or_non_monotonic_evidence_is_detected() -> None:
    assert not contradictory_evidence([(0, "pass"), (2, "regression")])
    assert contradictory_evidence([(0, "regression"), (2, "pass")])
    assert contradictory_evidence([(2, "pass"), (2, "regression")])


@pytest.mark.usefixtures("clean_database")
def test_analysis_contract_create_start_list_and_detail(tmp_path: Path) -> None:
    with TestClient(create_app()) as client:
        session, _, _ = _planned_session(client, tmp_path)
        created = client.post(
            f"/api/v1/bisection-sessions/{session['id']}/analyses",
            json={"configuration": {"executable": sys.executable}},
        )
        assert created.status_code == 201, created.text
        analysis = created.json()
        assert analysis["status"] == "pending"
        assert analysis["good_position"] == -1
        assert analysis["good_commit_sha"] == session["baseline_commit_sha"]
        assert analysis["bad_position"] == session["commit_count"] - 1
        assert analysis["bad_commit_sha"] == session["candidate_commit_sha"]

        conflict = client.post(
            f"/api/v1/bisection-sessions/{session['id']}/analyses",
            json={"configuration": {"executable": sys.executable}},
        )
        assert conflict.status_code == 409
        listing = client.get(f"/api/v1/bisection-sessions/{session['id']}/analyses")
        assert listing.status_code == 200
        assert listing.json()["items"][0]["id"] == analysis["id"]
        detail = client.get(f"/api/v1/bisection-analyses/{analysis['id']}")
        assert detail.status_code == 200
        assert detail.json()["steps"] == []
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                "agentscope_api.api.routes.bisection_analysis.enqueue_bisection_analysis",
                lambda _analysis_id: None,
            )
            started = client.post(f"/api/v1/bisection-analyses/{analysis['id']}/execute")
        assert started.status_code == 202
        assert started.json()["status"] == "queued"

    async def violate_active_constraint() -> None:
        async with SessionLocal() as database:
            database.add(
                BisectionAnalysisRecord(
                    session_id=UUID(str(session["id"])),
                    configuration={"executable": sys.executable},
                    repository_fingerprint=str(session["repository_fingerprint"]),
                    baseline_commit_sha=str(session["baseline_commit_sha"]),
                    candidate_commit_sha=str(session["candidate_commit_sha"]),
                    total_commit_count=int(session["commit_count"]),
                    good_position=-1,
                    good_commit_sha=str(session["baseline_commit_sha"]),
                    bad_position=int(session["commit_count"]) - 1,
                    bad_commit_sha=str(session["candidate_commit_sha"]),
                )
            )
            with pytest.raises(IntegrityError):
                await database.commit()

    asyncio.run(violate_active_constraint())


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


def _session_with_history(
    client: TestClient, tmp_path: Path, *, commit_count: int = 6
) -> tuple[dict[str, object], Path, list[str]]:
    repository, commits = _repository(tmp_path, commit_count=commit_count)
    check = _regression_check(client, commits[0], commits[-1])
    response = _create_session(client, check["id"], repository)
    assert response.status_code == 201, response.text
    return response.json(), repository, commits


def _probe(boundary: int, *, indeterminate: set[int] | None = None) -> dict[str, object]:
    indeterminate = indeterminate or set()
    code = (
        "from pathlib import Path; v=int(Path('version.txt').read_text()); "
        f"raise SystemExit(2 if v in {sorted(indeterminate)!r} else (0 if v < {boundary} else 1))"
    )
    return {
        "executable": sys.executable,
        "args": ["-c", code],
        "timeout_seconds": 10,
    }


def _create_and_start_analysis(
    client: TestClient, session_id: object, configuration: dict[str, object]
) -> dict[str, object]:
    created = client.post(
        f"/api/v1/bisection-sessions/{session_id}/analyses",
        json={"configuration": configuration},
    )
    assert created.status_code == 201, created.text
    analysis = created.json()
    with patch("agentscope_api.api.routes.bisection_analysis.enqueue_bisection_analysis"):
        started = client.post(f"/api/v1/bisection-analyses/{analysis['id']}/execute")
    assert started.status_code == 202, started.text
    return analysis


def _seed_execution(
    client: TestClient,
    session_id: object,
    commit_sha: str,
    configuration: dict[str, object],
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/bisection-sessions/{session_id}/execution-runs",
        json={"configuration": configuration},
    )
    assert response.status_code == 201, response.text
    run = response.json()
    with patch("agentscope_api.api.routes.bisection_execution.enqueue_bisection_execution"):
        submitted = client.post(
            f"/api/v1/bisection-execution-runs/{run['id']}/execute",
            json={"commit_shas": [commit_sha]},
        )
    assert submitted.status_code == 202, submitted.text
    for _ in range(3):
        outcome = asyncio.run(process_execution_message(UUID(str(run["id"]))))
        if outcome is not ProcessOutcome.REQUEUED:
            break
    return run


def _drive_analysis(client: TestClient, analysis_id: object) -> dict[str, object]:
    parsed = UUID(str(analysis_id))
    for _ in range(30):
        result = asyncio.run(process_analysis_message(parsed))
        if result.execution_run_id is not None:
            for _ in range(3):
                execution = asyncio.run(process_execution_message(result.execution_run_id))
                if execution is not ProcessOutcome.REQUEUED:
                    break
        detail = client.get(f"/api/v1/bisection-analyses/{analysis_id}").json()
        if detail["status"] in {"attributed", "inconclusive", "failed"}:
            return detail
    raise AssertionError("analysis did not terminate")


@pytest.mark.usefixtures("clean_database")
def test_linear_history_uses_midpoints_and_attributes_first_regressed_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BISECTION_WORK_ROOT", str(tmp_path / "analysis work root"))
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            session, repository, commits = _session_with_history(client, tmp_path)
            (repository / "version.txt").write_text("99", encoding="utf-8")
            _git(repository, "add", "version.txt")
            _git(repository, "commit", "-m", "branch moved after planning")
            before = (
                _git(repository, "rev-parse", "HEAD"),
                _git(repository, "branch", "--show-current"),
                _git(repository, "status", "--porcelain=v1"),
            )
            analysis = _create_and_start_analysis(client, session["id"], _probe(3))
            detail = _drive_analysis(client, analysis["id"])

        assert detail["status"] == "attributed"
        assert detail["final_good_commit_sha"] == commits[2]
        assert detail["final_bad_commit_sha"] == commits[3]
        assert [step["selected_position"] for step in detail["steps"]] == [4, 1, 2]
        assert [step["decision"] for step in detail["steps"]] == [
            "confirm_candidate",
            "advance_good",
            "retreat_bad",
        ]
        assert detail["new_evidence_count"] == 3
        assert detail["reused_evidence_count"] == 0
        assert before == (
            _git(repository, "rev-parse", "HEAD"),
            _git(repository, "branch", "--show-current"),
            _git(repository, "status", "--porcelain=v1"),
        )
    finally:
        get_settings.cache_clear()


@pytest.mark.usefixtures("clean_database")
def test_indeterminate_midpoint_is_skipped_without_looping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BISECTION_WORK_ROOT", str(tmp_path / "indeterminate root"))
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            session, _, commits = _session_with_history(client, tmp_path)
            analysis = _create_and_start_analysis(
                client, session["id"], _probe(4, indeterminate={2})
            )
            detail = _drive_analysis(client, analysis["id"])
        assert detail["status"] == "attributed"
        assert detail["final_good_commit_sha"] == commits[3]
        assert detail["final_bad_commit_sha"] == commits[4]
        skipped = [step for step in detail["steps"] if step["decision"] == "skip_indeterminate"]
        assert [step["selected_commit_sha"] for step in skipped] == [commits[2]]
        assert len({step["selected_position"] for step in detail["steps"]}) == len(detail["steps"])
    finally:
        get_settings.cache_clear()


@pytest.mark.usefixtures("clean_database")
def test_compatible_evidence_is_reused_but_incompatible_configuration_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BISECTION_WORK_ROOT", str(tmp_path / "reuse root"))
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            session, _, commits = _session_with_history(client, tmp_path)
            configuration = _probe(3)
            run = _seed_execution(client, session["id"], commits[-1], configuration)
            passed_run = _seed_execution(client, session["id"], commits[2], configuration)
            analysis = _create_and_start_analysis(client, session["id"], configuration)
            first = asyncio.run(process_analysis_message(UUID(str(analysis["id"]))))
            second = asyncio.run(process_analysis_message(UUID(str(analysis["id"]))))
            detail = client.get(f"/api/v1/bisection-analyses/{analysis['id']}").json()
            completed = _drive_analysis(client, analysis["id"])
            original_run = client.get(f"/api/v1/bisection-execution-runs/{run['id']}").json()
            incompatible = _create_and_start_analysis(client, session["id"], _probe(4))
            incompatible_first = asyncio.run(
                process_analysis_message(UUID(str(incompatible["id"])))
            )
            historical = client.get(f"/api/v1/bisection-analyses/{analysis['id']}").json()
        assert first.outcome is AnalysisProcessOutcome.REQUEUED
        assert detail["steps"][0]["evidence_source"] == "reused"
        assert detail["steps"][0]["execution_run_id"] == run["id"]
        assert second.outcome is AnalysisProcessOutcome.REQUEUED
        assert detail["steps"][1]["evidence_source"] == "reused"
        assert detail["steps"][1]["execution_run_id"] == passed_run["id"]
        assert incompatible_first.outcome is AnalysisProcessOutcome.WAITING
        assert incompatible_first.execution_created
        assert incompatible_first.execution_run_id != UUID(str(run["id"]))
        assert historical == completed
        assert original_run["targets"][0]["outcome"] == "regression"
    finally:
        get_settings.cache_clear()


@pytest.mark.usefixtures("clean_database")
def test_duplicate_analysis_delivery_does_not_duplicate_execution_request(tmp_path: Path) -> None:
    with TestClient(create_app()) as client:
        session, _, _ = _session_with_history(client, tmp_path)
        analysis = _create_and_start_analysis(client, session["id"], _probe(3))
        first = asyncio.run(process_analysis_message(UUID(str(analysis["id"]))))
        second = asyncio.run(process_analysis_message(UUID(str(analysis["id"]))))
        detail = client.get(f"/api/v1/bisection-analyses/{analysis['id']}").json()
        runs = client.get(f"/api/v1/bisection-sessions/{session['id']}/execution-runs").json()
    assert first.outcome is second.outcome is AnalysisProcessOutcome.WAITING
    assert first.execution_run_id is not None
    assert second.execution_run_id is None
    assert len(runs["items"]) == 1
    assert detail["steps"] == []


@pytest.mark.usefixtures("clean_database")
def test_candidate_execution_failure_is_inconclusive_not_regression(tmp_path: Path) -> None:
    with TestClient(create_app()) as client:
        session, _, _ = _session_with_history(client, tmp_path)
        analysis = _create_and_start_analysis(
            client,
            session["id"],
            {"executable": "agentscope-definitely-missing-executable", "timeout_seconds": 1},
        )
        detail = _drive_analysis(client, analysis["id"])
    assert detail["status"] == "inconclusive"
    assert detail["terminal_reason"] == "candidate_execution_failed"
    assert detail["execution_failure_count"] == 1
    assert detail["steps"][0]["observed_outcome"] == "execution_failed"


@pytest.mark.usefixtures("clean_database")
@pytest.mark.parametrize(
    ("exit_code", "reason"),
    [(0, "candidate_probe_pass"), (2, "candidate_probe_indeterminate")],
)
def test_candidate_must_confirm_regression(tmp_path: Path, exit_code: int, reason: str) -> None:
    with TestClient(create_app()) as client:
        session, _, _ = _session_with_history(client, tmp_path)
        analysis = _create_and_start_analysis(
            client,
            session["id"],
            {
                "executable": sys.executable,
                "args": ["-c", f"raise SystemExit({exit_code})"],
                "timeout_seconds": 10,
            },
        )
        detail = _drive_analysis(client, analysis["id"])
    assert detail["status"] == "inconclusive"
    assert detail["terminal_reason"] == reason


@pytest.mark.usefixtures("clean_database")
def test_indeterminate_gap_terminates_inconclusive(tmp_path: Path) -> None:
    configuration = _probe(5, indeterminate={1, 2, 3, 4})
    with TestClient(create_app()) as client:
        session, _, _ = _session_with_history(client, tmp_path)
        analysis = _create_and_start_analysis(client, session["id"], configuration)
        detail = _drive_analysis(client, analysis["id"])
    assert detail["status"] == "inconclusive"
    assert detail["terminal_reason"] == "indeterminate_gap"
    assert detail["indeterminate_count"] == 4
    assert len({step["selected_position"] for step in detail["steps"]}) == len(detail["steps"])


@pytest.mark.usefixtures("clean_database")
def test_non_monotonic_compatible_evidence_blocks_attribution(tmp_path: Path) -> None:
    code = (
        "from pathlib import Path; v=int(Path('version.txt').read_text()); "
        "raise SystemExit(1 if v in {1, 3, 4, 5} else 0)"
    )
    configuration = {
        "executable": sys.executable,
        "args": ["-c", code],
        "timeout_seconds": 10,
    }
    with TestClient(create_app()) as client:
        session, _, commits = _session_with_history(client, tmp_path)
        _seed_execution(client, session["id"], commits[1], configuration)
        _seed_execution(client, session["id"], commits[2], configuration)
        analysis = _create_and_start_analysis(client, session["id"], configuration)
        result = asyncio.run(process_analysis_message(UUID(str(analysis["id"]))))
        detail = client.get(f"/api/v1/bisection-analyses/{analysis['id']}").json()
    assert result.outcome is AnalysisProcessOutcome.INCONCLUSIVE
    assert detail["terminal_reason"] == "inconsistent_evidence"
    assert detail["final_bad_commit_sha"] is None


@pytest.mark.usefixtures("clean_database")
def test_expired_lease_is_recovered_and_stale_owner_is_fenced(tmp_path: Path) -> None:
    with TestClient(create_app()) as client:
        session, _, _ = _session_with_history(client, tmp_path)
        analysis = _create_and_start_analysis(client, session["id"], _probe(3))
    analysis_id = UUID(str(analysis["id"]))

    async def exercise() -> AnalysisProcessOutcome:
        now = datetime.now(UTC)
        async with SessionLocal() as database:
            stale = await claim_analysis(database, analysis_id, now=now)
        assert stale is not None
        recovery_time = now + timedelta(minutes=3)
        async with SessionLocal() as database:
            assert await recover_analyses(database, now=recovery_time) == [analysis_id]
        async with SessionLocal() as database:
            current = await claim_analysis(database, analysis_id, now=recovery_time)
        assert current is not None and current.token != stale.token
        stale_result = await advance_analysis_claim(stale)
        return stale_result.outcome

    assert asyncio.run(exercise()) is AnalysisProcessOutcome.IGNORED


@pytest.mark.usefixtures("clean_database")
def test_broker_outage_leaves_durable_analysis_for_recovery(tmp_path: Path) -> None:
    with TestClient(create_app()) as client:
        session, _, _ = _session_with_history(client, tmp_path)
        created = client.post(
            f"/api/v1/bisection-sessions/{session['id']}/analyses",
            json={"configuration": _probe(3)},
        ).json()
        with patch(
            "agentscope_api.api.routes.bisection_analysis.enqueue_bisection_analysis",
            side_effect=RuntimeError("broker unavailable"),
        ):
            started = client.post(f"/api/v1/bisection-analyses/{created['id']}/execute")
    sent: list[UUID] = []
    assert started.json()["queue_delivery"] == "deferred"
    assert asyncio.run(recover_bisection_analyses_once(enqueue=sent.append)) == 1
    assert sent == [UUID(str(created["id"]))]
    assert asyncio.run(recover_bisection_analyses_once(enqueue=sent.append)) == 0
    continued = asyncio.run(process_analysis_message(sent[0]))
    assert continued.outcome is AnalysisProcessOutcome.WAITING
    assert continued.execution_created


@pytest.mark.usefixtures("clean_database")
def test_candidate_immediately_after_baseline_is_attributed_after_confirmation(
    tmp_path: Path,
) -> None:
    with TestClient(create_app()) as client:
        session, _, commits = _session_with_history(client, tmp_path, commit_count=2)
        analysis = _create_and_start_analysis(client, session["id"], _probe(1))
        detail = _drive_analysis(client, analysis["id"])
    assert detail["status"] == "attributed"
    assert detail["final_good_commit_sha"] == commits[0]
    assert detail["final_good_position"] == -1
    assert detail["final_bad_commit_sha"] == commits[1]
    assert detail["final_bad_position"] == 0
    assert len(detail["steps"]) == 1
    with TestClient(create_app()) as client:
        immutable = client.post(f"/api/v1/bisection-analyses/{analysis['id']}/execute")
        unchanged = client.get(f"/api/v1/bisection-analyses/{analysis['id']}").json()
    assert immutable.status_code == 409
    assert unchanged == detail


@pytest.mark.usefixtures("clean_database")
def test_execution_failure_gap_terminates_inconclusive(tmp_path: Path) -> None:
    code = (
        "from pathlib import Path; import time; v=int(Path('version.txt').read_text()); "
        "raise SystemExit(1) if v == 5 else time.sleep(2)"
    )
    configuration = {
        "executable": sys.executable,
        "args": ["-c", code],
        "timeout_seconds": 1,
    }
    with TestClient(create_app()) as client:
        session, _, _ = _session_with_history(client, tmp_path)
        analysis = _create_and_start_analysis(client, session["id"], configuration)
        detail = _drive_analysis(client, analysis["id"])
    assert detail["status"] == "inconclusive"
    assert detail["terminal_reason"] == "execution_failure_gap"
    assert detail["execution_failure_count"] == 4
    assert all(step["observed_outcome"] == "execution_failed" for step in detail["steps"][1:])
