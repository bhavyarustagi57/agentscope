from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import BinaryIO, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_regressions import _analyzed_run, _create_check, _policy

from agentscope_api.core.config import get_settings
from agentscope_api.database import SessionLocal
from agentscope_api.main import create_app
from agentscope_api.models.bisection import BisectionSessionRecord
from agentscope_api.models.experiment import ExperimentVariantRecord
from agentscope_api.services.git_inspection import (
    MAX_GIT_OUTPUT_BYTES,
    GitCommandTimeout,
    GitInspector,
    MalformedGitOutput,
    UnsafeRepositoryState,
)

pytestmark = pytest.mark.usefixtures("clean_database")


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


def _repository(tmp_path: Path, *, commit_count: int = 4) -> tuple[Path, list[str]]:
    repository = tmp_path / "target-repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "AgentScope Test")
    _git(repository, "config", "user.email", "agentscope-test@example.invalid")
    commits: list[str] = []
    for position in range(commit_count):
        (repository / "version.txt").write_text(str(position), encoding="utf-8")
        _git(repository, "add", "version.txt")
        _git(repository, "commit", "-m", f"version {position}")
        commits.append(_git(repository, "rev-parse", "HEAD"))
    return repository, commits


def _regression_check(
    client: TestClient,
    baseline_revision: str | None,
    candidate_revision: str | None,
    *,
    detected: bool = True,
) -> dict[str, object]:
    pairs = (
        [("passed", "passed")] * 3 + [("passed", "failed")]
        if detected
        else [("passed", "passed"), ("failed", "failed")]
    )
    run, _ = _analyzed_run(client, [pairs])

    async def add_git_provenance() -> None:
        async with SessionLocal() as session, session.begin():
            variants = list(
                await session.scalars(
                    select(ExperimentVariantRecord).where(
                        ExperimentVariantRecord.experiment_id == UUID(str(run["experiment_id"]))
                    )
                )
            )
            for variant in variants:
                revision = baseline_revision if variant.variant_key == "A" else candidate_revision
                variant.provenance = (
                    {"git_commit_sha": revision, "agent_version": variant.variant_key}
                    if revision is not None
                    else {"agent_version": variant.variant_key}
                )

    asyncio.run(add_git_provenance())
    policy = _policy(client)
    response = _create_check(client, run["id"], policy["id"])
    assert response.status_code == 201, response.text
    return response.json()


def _create_session(client: TestClient, check_id: object, repository: Path):
    return client.post(
        "/api/v1/bisection-sessions",
        json={"regression_check_id": check_id, "repository_path": str(repository)},
    )


def test_linear_plan_freezes_ordered_range_metadata_and_linkage(tmp_path: Path) -> None:
    repository, commits = _repository(tmp_path)
    head_before = _git(repository, "rev-parse", "HEAD")
    status_before = _git(repository, "status", "--porcelain=v1")
    with TestClient(create_app()) as client:
        check = _regression_check(client, commits[0][:12], "main")
        created = _create_session(client, check["id"], repository)
        listing = client.get(
            "/api/v1/bisection-sessions",
            params={"regression_check_id": check["id"], "page_size": 1},
        )
        fetched = client.get(f"/api/v1/bisection-sessions/{created.json()['id']}")

    assert created.status_code == 201, created.text
    body = created.json()
    assert body == fetched.json()
    assert listing.json()["items"][0]["id"] == body["id"]
    assert body["status"] == "ready"
    assert body["regression_check_id"] == check["id"]
    assert body["baseline_revision"] == commits[0][:12]
    assert body["candidate_revision"] == "main"
    assert body["baseline_commit_sha"] == commits[0]
    assert body["candidate_commit_sha"] == commits[-1]
    assert body["commit_count"] == 3
    assert [item["commit_sha"] for item in body["commits"]] == commits[1:]
    assert [item["position"] for item in body["commits"]] == [0, 1, 2]
    assert [item["subject"] for item in body["commits"]] == [
        "version 1",
        "version 2",
        "version 3",
    ]
    assert all(item["committed_at"] for item in body["commits"])
    assert body["baseline_provenance"] == check["baseline_provenance"]
    assert body["candidate_provenance"] == check["candidate_provenance"]
    assert _git(repository, "rev-parse", "HEAD") == head_before
    assert _git(repository, "status", "--porcelain=v1") == status_before


def test_branch_movement_does_not_change_persisted_plan(tmp_path: Path) -> None:
    repository, commits = _repository(tmp_path, commit_count=3)
    with TestClient(create_app()) as client:
        check = _regression_check(client, commits[0], "main")
        created = _create_session(client, check["id"], repository).json()
        (repository / "version.txt").write_text("moved", encoding="utf-8")
        _git(repository, "add", "version.txt")
        _git(repository, "commit", "-m", "branch moved")
        fetched = client.get(f"/api/v1/bisection-sessions/{created['id']}").json()

    assert fetched == created
    assert fetched["candidate_commit_sha"] == commits[-1]


def test_merge_plan_preserves_candidate_parents_and_stable_order(tmp_path: Path) -> None:
    repository, commits = _repository(tmp_path, commit_count=1)
    _git(repository, "switch", "-c", "feature")
    (repository / "feature.txt").write_text("feature", encoding="utf-8")
    _git(repository, "add", "feature.txt")
    _git(repository, "commit", "-m", "feature commit")
    feature = _git(repository, "rev-parse", "HEAD")
    _git(repository, "switch", "main")
    (repository / "main.txt").write_text("main", encoding="utf-8")
    _git(repository, "add", "main.txt")
    _git(repository, "commit", "-m", "main commit")
    main = _git(repository, "rev-parse", "HEAD")
    _git(repository, "merge", "--no-ff", "feature", "-m", "merge feature")
    candidate = _git(repository, "rev-parse", "HEAD")

    inspector = GitInspector()
    first = inspector.inspect(str(repository), commits[0], candidate, maximum_commit_count=10)
    second = inspector.inspect(str(repository), commits[0], candidate, maximum_commit_count=10)

    assert [item.commit_sha for item in first.commits] == [
        item.commit_sha for item in second.commits
    ]
    assert first.commits[-1].commit_sha == candidate
    assert set(first.commits[-1].parent_shas) == {feature, main}


def test_identical_endpoints_are_rejected_without_partial_session(tmp_path: Path) -> None:
    repository, commits = _repository(tmp_path, commit_count=1)
    with TestClient(create_app()) as client:
        check = _regression_check(client, commits[0], commits[0])
        response = _create_session(client, check["id"], repository)
        listing = client.get("/api/v1/bisection-sessions")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BISECTION_ENDPOINTS_IDENTICAL"
    assert listing.json()["items"] == []


def test_baseline_must_be_ancestor_of_candidate(tmp_path: Path) -> None:
    repository, commits = _repository(tmp_path, commit_count=1)
    base = commits[0]
    _git(repository, "switch", "-c", "baseline-side")
    (repository / "baseline.txt").write_text("baseline", encoding="utf-8")
    _git(repository, "add", "baseline.txt")
    _git(repository, "commit", "-m", "baseline side")
    baseline = _git(repository, "rev-parse", "HEAD")
    _git(repository, "switch", "-c", "candidate-side", base)
    (repository / "candidate.txt").write_text("candidate", encoding="utf-8")
    _git(repository, "add", "candidate.txt")
    _git(repository, "commit", "-m", "candidate side")
    candidate = _git(repository, "rev-parse", "HEAD")

    with TestClient(create_app()) as client:
        check = _regression_check(client, baseline, candidate)
        response = _create_session(client, check["id"], repository)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BASELINE_NOT_ANCESTOR"


@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [("missing", "GIT_REPOSITORY_NOT_FOUND"), ("directory", "NOT_A_GIT_REPOSITORY")],
)
def test_invalid_repository_is_rejected(tmp_path: Path, kind: str, expected_code: str) -> None:
    repository = tmp_path / kind
    if kind == "directory":
        repository.mkdir()
    with TestClient(create_app()) as client:
        check = _regression_check(client, "baseline", "candidate")
        response = _create_session(client, check["id"], repository)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == expected_code


@pytest.mark.parametrize(
    ("baseline", "candidate", "expected_code"),
    [
        (None, "candidate", "BASELINE_GIT_REVISION_REQUIRED"),
        ("baseline", None, "CANDIDATE_GIT_REVISION_REQUIRED"),
    ],
)
def test_missing_frozen_revision_is_rejected(
    tmp_path: Path,
    baseline: str | None,
    candidate: str | None,
    expected_code: str,
) -> None:
    repository, _ = _repository(tmp_path)
    with TestClient(create_app()) as client:
        check = _regression_check(client, baseline, candidate)
        response = _create_session(client, check["id"], repository)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == expected_code


def test_nonregression_check_cannot_plan_bisection(tmp_path: Path) -> None:
    repository, commits = _repository(tmp_path)
    with TestClient(create_app()) as client:
        check = _regression_check(client, commits[0], commits[-1], detected=False)
        response = _create_session(client, check["id"], repository)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REGRESSION_NOT_DETECTED"


def test_unknown_regression_check_is_rejected(tmp_path: Path) -> None:
    repository, _ = _repository(tmp_path)
    with TestClient(create_app()) as client:
        response = _create_session(client, "00000000-0000-0000-0000-000000000000", repository)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REGRESSION_CHECK_NOT_FOUND"


@pytest.mark.parametrize(
    ("baseline", "candidate", "expected_code"),
    [
        ("does-not-exist", "HEAD", "BASELINE_REVISION_NOT_FOUND"),
        ("HEAD~1", "does-not-exist", "CANDIDATE_REVISION_NOT_FOUND"),
    ],
)
def test_unresolvable_revision_is_rejected(
    tmp_path: Path, baseline: str, candidate: str, expected_code: str
) -> None:
    repository, _ = _repository(tmp_path)
    with TestClient(create_app()) as client:
        check = _regression_check(client, baseline, candidate)
        response = _create_session(client, check["id"], repository)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == expected_code


def test_commit_range_limit_rejects_without_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, commits = _repository(tmp_path, commit_count=4)
    monkeypatch.setenv("BISECTION_MAX_COMMIT_RANGE", "2")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            check = _regression_check(client, commits[0], commits[-1])
            response = _create_session(client, check["id"], repository)
            listing = client.get("/api/v1/bisection-sessions")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "COMMIT_RANGE_TOO_LARGE"
    assert listing.json()["items"] == []


def test_duplicate_plan_is_rejected(tmp_path: Path) -> None:
    repository, commits = _repository(tmp_path)
    with TestClient(create_app()) as client:
        check = _regression_check(client, commits[0], commits[-1])
        first = _create_session(client, check["id"], repository)
        duplicate = _create_session(client, check["id"], repository)

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "BISECTION_SESSION_EXISTS"


@pytest.mark.parametrize(
    ("failure", "status_code", "error_code"),
    [
        (GitCommandTimeout(), 504, "GIT_COMMAND_TIMEOUT"),
        (MalformedGitOutput(), 422, "MALFORMED_GIT_OUTPUT"),
        (UnsafeRepositoryState(), 422, "UNSAFE_GIT_REPOSITORY"),
    ],
)
def test_git_failures_are_bounded_and_leave_no_partial_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    status_code: int,
    error_code: str,
) -> None:
    repository, commits = _repository(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(GitInspector, "inspect", fail)
    with TestClient(create_app()) as client:
        check = _regression_check(client, commits[0], commits[-1])
        response = _create_session(client, check["id"], repository)
        listing = client.get("/api/v1/bisection-sessions")

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == error_code
    assert listing.json()["items"] == []


def test_session_database_constraints_reject_invalid_commit_count(tmp_path: Path) -> None:
    repository, commits = _repository(tmp_path)
    with TestClient(create_app()) as client:
        check = _regression_check(client, commits[0], commits[-1])
        created = _create_session(client, check["id"], repository).json()

    async def invalidate() -> None:
        async with SessionLocal() as session:
            with pytest.raises(IntegrityError):
                async with session.begin():
                    record = await session.get(BisectionSessionRecord, UUID(str(created["id"])))
                    assert record is not None
                    record.commit_count = 0

    asyncio.run(invalidate())


def test_git_runner_uses_argv_timeout_and_no_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, object, object]] = []

    def fake_run(args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((args, kwargs.get("timeout"), kwargs.get("shell")))
        cast(BinaryIO, kwargs["stdout"]).write(b"ok\n")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    output = GitInspector(timeout_seconds=3)._run(tmp_path, ["rev-parse", "HEAD"])

    assert output == "ok"
    assert calls == [(["git", "rev-parse", "HEAD"], 3, False)]


def test_git_timeout_is_a_bounded_domain_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def time_out(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(subprocess, "run", time_out)
    with pytest.raises(GitCommandTimeout):
        GitInspector(timeout_seconds=1)._run(tmp_path, ["rev-parse", "HEAD"])


def test_git_runner_rejects_oversized_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def produce_oversized_output(
        args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        cast(BinaryIO, kwargs["stdout"]).write(b"x" * (MAX_GIT_OUTPUT_BYTES + 1))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", produce_oversized_output)
    with pytest.raises(MalformedGitOutput):
        GitInspector()._run(tmp_path, ["rev-parse", "HEAD"])


def test_list_pagination_is_bounded() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/bisection-sessions", params={"page_size": 101})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
