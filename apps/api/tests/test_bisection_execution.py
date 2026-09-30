from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from test_bisections import _create_session, _regression_check, _repository

from agentscope_api.core.config import get_settings
from agentscope_api.database import SessionLocal
from agentscope_api.jobs.recover import recover_bisections_once
from agentscope_api.main import create_app
from agentscope_api.models.bisection_execution import BisectionExecutionRunRecord
from agentscope_api.schemas.bisection_execution import ProbeConfiguration
from agentscope_api.services.bisection_execution import (
    ClaimOutcome,
    ProcessOutcome,
    claim_execution_target,
    persist_execution_result,
    process_execution_message,
    recover_execution_runs,
)
from agentscope_api.services.bisection_worktree import (
    ProbeResult,
    WorktreeProbeRunner,
    classify_exit_code,
)


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _repository_with_spaces(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "target repository with spaces"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "AgentScope Test")
    _git(repository, "config", "user.email", "agentscope-test@example.invalid")
    (repository / "version.txt").write_text("one", encoding="utf-8")
    _git(repository, "add", "version.txt")
    _git(repository, "commit", "-m", "initial")
    return repository, _git(repository, "rev-parse", "HEAD")


def _planned_session(
    client: TestClient, tmp_path: Path
) -> tuple[dict[str, object], Path, list[str]]:
    repository, commits = _repository(tmp_path, commit_count=4)
    check = _regression_check(client, commits[0], commits[-1])
    response = _create_session(client, check["id"], repository)
    assert response.status_code == 201, response.text
    return response.json(), repository, commits


def _create_run(client: TestClient, session_id: object, exit_code: int = 0) -> dict[str, object]:
    response = client.post(
        f"/api/v1/bisection-sessions/{session_id}/execution-runs",
        json={
            "configuration": {
                "executable": sys.executable,
                "args": ["-c", f"raise SystemExit({exit_code})"],
                "timeout_seconds": 10,
            }
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _submit(client: TestClient, run_id: object, commits: list[str]):
    with patch("agentscope_api.api.routes.bisection_execution.enqueue_bisection_execution"):
        return client.post(
            f"/api/v1/bisection-execution-runs/{run_id}/execute",
            json={"commit_shas": commits},
        )


def test_probe_contract_is_argv_only_and_rejects_workdir_traversal() -> None:
    configuration = ProbeConfiguration(
        executable="python",
        args=["probe.py", "; echo not-a-shell"],
        working_directory="tests",
    )

    assert configuration.args[1] == "; echo not-a-shell"
    with pytest.raises(ValidationError):
        ProbeConfiguration(executable="python", working_directory="../outside")
    with pytest.raises(ValidationError):
        ProbeConfiguration(executable="python\nother")
    with pytest.raises(ValidationError):
        ProbeConfiguration.model_validate({"executable": "python", "args": "not-an-array"})


def test_detached_probe_preserves_target_and_bounds_literal_argv_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, commit_sha = _repository_with_spaces(tmp_path)
    real_run = subprocess.run
    shell_values: list[bool] = []

    def run_without_shell(*args, **kwargs):
        shell_values.append(kwargs.get("shell", False))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(
        "agentscope_api.services.bisection_worktree.subprocess.run", run_without_shell
    )
    before = (
        _git(repository, "rev-parse", "HEAD"),
        _git(repository, "branch", "--show-current"),
        _git(repository, "status", "--porcelain=v1"),
    )
    code = (
        "import sys; print(sys.argv[1]); print('e' * 100, file=sys.stderr); "
        "raise SystemExit(int(sys.argv[2]))"
    )
    result = WorktreeProbeRunner().execute(
        repository_root=str(repository),
        work_root=str(tmp_path / "managed work root with spaces"),
        run_id=uuid4(),
        commit_sha=commit_sha,
        token=uuid4(),
        configuration=ProbeConfiguration(
            executable=sys.executable,
            args=["-c", code, "; echo still-literal", "1"],
            max_stdout_bytes=12,
            max_stderr_bytes=8,
        ),
    )

    assert result.exit_code == 1
    assert classify_exit_code(result.exit_code) == "regression"
    assert result.stdout == "; echo still"
    assert result.stdout_truncated
    assert result.stderr == "eeeeeeee"
    assert result.stderr_truncated
    assert not result.cleanup_failed
    assert shell_values and not any(shell_values)
    assert before == (
        _git(repository, "rev-parse", "HEAD"),
        _git(repository, "branch", "--show-current"),
        _git(repository, "status", "--porcelain=v1"),
    )


def test_probe_timeout_spawn_failure_and_unexpected_exit_are_not_regressions(
    tmp_path: Path,
) -> None:
    repository, commit_sha = _repository_with_spaces(tmp_path)
    runner = WorktreeProbeRunner()
    timed_out = runner.execute(
        repository_root=str(repository),
        work_root=str(tmp_path / "work-timeout"),
        run_id=uuid4(),
        commit_sha=commit_sha,
        token=uuid4(),
        configuration=ProbeConfiguration(
            executable=sys.executable,
            args=["-c", "import time; time.sleep(5)"],
            timeout_seconds=1,
        ),
    )
    missing = runner.execute(
        repository_root=str(repository),
        work_root=str(tmp_path / "work-missing"),
        run_id=uuid4(),
        commit_sha=commit_sha,
        token=uuid4(),
        configuration=ProbeConfiguration(executable="definitely-not-an-executable-agentscope"),
    )

    assert timed_out.timed_out and timed_out.failure_kind == "timeout"
    assert missing.failure_kind == "spawn_failed"
    assert classify_exit_code(0) == "pass"
    assert classify_exit_code(2) == "indeterminate"
    assert classify_exit_code(7) == "indeterminate"


def test_cleanup_refuses_paths_outside_owned_work_root(tmp_path: Path) -> None:
    repository, _ = _repository_with_spaces(tmp_path)
    work_root = tmp_path / "managed-root"
    work_root.mkdir()
    outside = tmp_path / "unrelated" / "nested" / "directory"
    outside.mkdir(parents=True)
    sentinel = outside / "keep.txt"
    sentinel.write_text("do not delete", encoding="utf-8")

    failed, message = WorktreeProbeRunner()._cleanup(
        repository,
        work_root,
        outside,
        {"run_id": str(uuid4()), "commit_sha": "a" * 40, "token": str(uuid4())},
        worktree_added=False,
    )

    assert failed and message == "AgentScope-owned worktree cleanup failed"
    assert sentinel.read_text(encoding="utf-8") == "do not delete"


@pytest.mark.usefixtures("clean_database")
def test_run_contract_snapshots_config_rejects_external_sha_and_enforces_one_active(
    tmp_path: Path,
) -> None:
    with TestClient(create_app()) as client:
        session, _, commits = _planned_session(client, tmp_path)
        run = _create_run(client, session["id"])
        conflict = client.post(
            f"/api/v1/bisection-sessions/{session['id']}/execution-runs",
            json={"configuration": {"executable": sys.executable}},
        )
        external = _submit(client, run["id"], ["a" * 40])
        submitted = _submit(client, run["id"], [commits[1], commits[3]])
        repeated = _submit(client, run["id"], [commits[1], commits[3]])
        changed = _submit(client, run["id"], [commits[2]])
        detail = client.get(f"/api/v1/bisection-execution-runs/{run['id']}")
        listing = client.get(
            f"/api/v1/bisection-sessions/{session['id']}/execution-runs",
            params={"page_size": 1},
        )

    async def violate_active_run_constraint() -> None:
        async with SessionLocal() as database:
            database.add(
                BisectionExecutionRunRecord(
                    session_id=UUID(str(session["id"])),
                    configuration={"executable": sys.executable},
                    repository_fingerprint=str(session["repository_fingerprint"]),
                    repository_root=str(session["repository_root"]),
                    planned_commit_count=int(session["commit_count"]),
                )
            )
            with pytest.raises(IntegrityError):
                await database.commit()

    asyncio.run(violate_active_run_constraint())

    assert run["status"] == "pending"
    assert run["configuration"]["executable"] == sys.executable
    assert run["repository_fingerprint"] == session["repository_fingerprint"]
    assert conflict.status_code == 409
    assert external.status_code == 422
    assert submitted.status_code == repeated.status_code == 202
    assert changed.status_code == 409
    assert detail.json()["status"] == "queued"
    assert detail.json()["requested_commit_count"] == 2
    assert {item["commit_sha"] for item in detail.json()["targets"]} == {
        commits[1],
        commits[3],
    }
    assert listing.json()["items"][0]["id"] == run["id"]


@pytest.mark.usefixtures("clean_database")
def test_worker_maps_all_exit_codes_and_keeps_historical_runs_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BISECTION_WORK_ROOT", str(tmp_path / "worker root with spaces"))
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            session, repository, commits = _planned_session(client, tmp_path)
            before = (
                _git(repository, "rev-parse", "HEAD"),
                _git(repository, "branch", "--show-current"),
                _git(repository, "status", "--porcelain=v1"),
            )
            observed: list[str] = []
            run_ids: list[str] = []
            for exit_code in (0, 1, 2, 7):
                run = _create_run(client, session["id"], exit_code)
                assert _submit(client, run["id"], [commits[2]]).status_code == 202
                assert (
                    asyncio.run(process_execution_message(UUID(str(run["id"]))))
                    is ProcessOutcome.COMPLETED
                )
                assert (
                    asyncio.run(process_execution_message(UUID(str(run["id"]))))
                    is ProcessOutcome.IGNORED
                )
                detail = client.get(f"/api/v1/bisection-execution-runs/{run['id']}").json()
                observed.append(detail["targets"][0]["outcome"])
                assert len(detail["targets"][0]["attempts"]) == 1
                run_ids.append(str(run["id"]))
        assert observed == ["pass", "regression", "indeterminate", "indeterminate"]
        assert len(set(run_ids)) == 4
        assert before == (
            _git(repository, "rev-parse", "HEAD"),
            _git(repository, "branch", "--show-current"),
            _git(repository, "status", "--porcelain=v1"),
        )
    finally:
        get_settings.cache_clear()


@pytest.mark.usefixtures("clean_database")
def test_timeout_retries_are_bounded_and_finish_as_execution_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BISECTION_WORK_ROOT", str(tmp_path / "timeout-root"))
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            session, _, commits = _planned_session(client, tmp_path)
            response = client.post(
                f"/api/v1/bisection-sessions/{session['id']}/execution-runs",
                json={
                    "configuration": {
                        "executable": sys.executable,
                        "args": ["-c", "import time; time.sleep(5)"],
                        "timeout_seconds": 1,
                    }
                },
            )
            run = response.json()
            assert _submit(client, run["id"], [commits[1]]).status_code == 202
        outcomes = [asyncio.run(process_execution_message(UUID(str(run["id"])))) for _ in range(3)]
        with TestClient(create_app()) as client:
            detail = client.get(f"/api/v1/bisection-execution-runs/{run['id']}").json()
        assert outcomes == [ProcessOutcome.REQUEUED, ProcessOutcome.REQUEUED, ProcessOutcome.FAILED]
        assert detail["status"] == "failed"
        assert detail["execution_failure_count"] == 1
        assert detail["targets"][0]["outcome"] == "execution_failed"
        assert detail["targets"][0]["attempt_count"] == 3
        assert len(detail["targets"][0]["attempts"]) == 3
        assert all(attempt["timed_out"] for attempt in detail["targets"][0]["attempts"])
    finally:
        get_settings.cache_clear()


@pytest.mark.usefixtures("clean_database")
def test_partial_progress_expired_lease_recovery_and_stale_write_fencing(
    tmp_path: Path,
) -> None:
    with TestClient(create_app()) as client:
        session, _, commits = _planned_session(client, tmp_path)
        run = _create_run(client, session["id"])
        assert _submit(client, run["id"], [commits[1], commits[2]]).status_code == 202
    run_id = UUID(str(run["id"]))

    async def exercise() -> tuple[ClaimOutcome, ProcessOutcome, ProcessOutcome]:
        async with SessionLocal() as database:
            first = await claim_execution_target(database, run_id)
        assert first.outcome is ClaimOutcome.CLAIMED
        assert (
            await persist_execution_result(first, ProbeResult(exit_code=0, duration_ms=1))
            is ProcessOutcome.REQUEUED
        )
        async with SessionLocal() as database:
            old = await claim_execution_target(database, run_id)
        assert old.outcome is ClaimOutcome.CLAIMED
        recovery_time = datetime.now(UTC) + timedelta(minutes=10)
        async with SessionLocal() as database:
            assert await recover_execution_runs(database, now=recovery_time) == [run_id]
        async with SessionLocal() as database:
            current = await claim_execution_target(database, run_id, now=recovery_time)
        assert current.attempt == 2
        stale = await persist_execution_result(old, ProbeResult(exit_code=1, duration_ms=1))
        finished = await persist_execution_result(current, ProbeResult(exit_code=0, duration_ms=1))
        return current.outcome, stale, finished

    outcome, stale, finished = asyncio.run(exercise())
    assert outcome is ClaimOutcome.CLAIMED
    assert stale is ProcessOutcome.IGNORED
    assert finished is ProcessOutcome.COMPLETED
    with TestClient(create_app()) as client:
        detail = client.get(f"/api/v1/bisection-execution-runs/{run_id}").json()
    assert [target["outcome"] for target in detail["targets"]] == ["pass", "pass"]
    assert len(detail["targets"][1]["attempts"]) == 2
    assert detail["targets"][1]["attempts"][0]["failure_kind"] == "lease_expired"


@pytest.mark.usefixtures("clean_database")
def test_broker_failure_leaves_intent_for_periodic_recovery(tmp_path: Path) -> None:
    with TestClient(create_app()) as client:
        session, _, commits = _planned_session(client, tmp_path)
        run = _create_run(client, session["id"])
        with patch(
            "agentscope_api.api.routes.bisection_execution.enqueue_bisection_execution",
            side_effect=RuntimeError("broker unavailable"),
        ):
            submitted = client.post(
                f"/api/v1/bisection-execution-runs/{run['id']}/execute",
                json={"commit_shas": [commits[1]]},
            )
    sent: list[UUID] = []
    assert submitted.json()["queue_delivery"] == "deferred"
    assert asyncio.run(recover_bisections_once(enqueue=sent.append)) == 1
    assert sent == [UUID(str(run["id"]))]
    assert asyncio.run(recover_bisections_once(enqueue=sent.append)) == 0


@pytest.mark.usefixtures("clean_database")
def test_cleanup_failure_is_evidence_not_domain_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BISECTION_WORK_ROOT", str(tmp_path / "cleanup-root"))
    get_settings.cache_clear()
    monkeypatch.setattr(
        WorktreeProbeRunner,
        "_cleanup",
        lambda *args, **kwargs: (True, "AgentScope-owned worktree cleanup failed"),
    )
    try:
        with TestClient(create_app()) as client:
            session, _, commits = _planned_session(client, tmp_path)
            run = _create_run(client, session["id"])
            assert _submit(client, run["id"], [commits[1]]).status_code == 202
        assert (
            asyncio.run(process_execution_message(UUID(str(run["id"])))) is ProcessOutcome.COMPLETED
        )
        with TestClient(create_app()) as client:
            target = client.get(f"/api/v1/bisection-execution-runs/{run['id']}").json()["targets"][
                0
            ]
        assert target["outcome"] == "pass"
        assert target["cleanup_failed"] is True
    finally:
        get_settings.cache_clear()
