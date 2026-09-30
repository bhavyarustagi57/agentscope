"""Run the local AgentScope Git-bisection demo in a tool-owned temporary repository."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from seed_demo import DEMO, Api, _seed_experiment, _seed_regression, seed_demo

from agentscope_api.core.config import get_settings
from agentscope_api.database import SessionLocal
from agentscope_api.services.bisection_analysis import start_analysis
from agentscope_api.services.bisection_execution import ProcessOutcome, process_execution_message
from agentscope_api.services.bisection_orchestration import process_analysis_message

MARKER = "AgentScope deterministic bisection demo v1\n"


def _git(repository: Path, *args: str, commit_position: int | None = None) -> str:
    environment = os.environ.copy()
    if commit_position is not None:
        timestamp = f"2026-09-20T00:00:0{commit_position}+00:00"
        environment.update({"GIT_AUTHOR_DATE": timestamp, "GIT_COMMITTER_DATE": timestamp})
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        shell=False,
        env=environment,
    )
    return result.stdout.strip()


def _owned_repository(workspace: Path) -> tuple[Path, list[str]]:
    workspace = workspace.resolve()
    marker = workspace / ".agentscope-demo-owned"
    repository = (workspace / "repository").resolve()
    try:
        repository.relative_to(workspace)
    except ValueError as error:
        raise RuntimeError("refusing repository outside the demo workspace") from error
    if workspace.exists():
        if not marker.is_file() or marker.read_text(encoding="utf-8") != MARKER:
            raise RuntimeError(f"refusing non-demo workspace: {workspace}")
    else:
        workspace.mkdir(parents=True)
        marker.write_text(MARKER, encoding="utf-8")
    if not repository.exists():
        repository.mkdir()
        _git(repository, "init", "-b", "main")
        _git(repository, "config", "user.name", "AgentScope Demo")
        _git(repository, "config", "user.email", "agentscope-demo@example.invalid")
        for position in range(5):
            (repository / "version.txt").write_text(str(position), encoding="utf-8")
            _git(repository, "add", "version.txt")
            _git(repository, "commit", "-m", f"demo version {position}", commit_position=position)
    commits = _git(repository, "rev-list", "--reverse", "HEAD").splitlines()
    values = [_git(repository, "show", f"{commit}:version.txt") for commit in commits]
    if len(commits) != 5 or values != ["0", "1", "2", "3", "4"]:
        raise RuntimeError("owned demo repository does not match the deterministic fixture")
    return repository, commits


async def _start(analysis_id: UUID) -> None:
    async with SessionLocal() as session:
        await start_analysis(session, analysis_id)


async def _drive(analysis_id: UUID) -> None:
    for _ in range(30):
        result = await process_analysis_message(analysis_id)
        if result.execution_run_id is not None:
            for _ in range(3):
                execution = await process_execution_message(result.execution_run_id)
                if execution is not ProcessOutcome.REQUEUED:
                    break
        if result.outcome.value in {"attributed", "inconclusive", "failed"}:
            return
    raise RuntimeError("demo bisection analysis did not terminate")


def run_bisection_demo(
    api: Any,
    workspace: Path,
    run_async: Any = None,
) -> dict[str, Any]:
    if run_async is None:
        with asyncio.Runner() as event_loop:
            return run_bisection_demo(
                api, workspace, lambda function, *args: event_loop.run(function(*args))
            )
    runner = run_async
    workspace = workspace.resolve()
    repository, commits = _owned_repository(workspace)
    worktree_root = (workspace / "worktrees").resolve()
    try:
        worktree_root.relative_to(workspace)
    except ValueError as error:
        raise RuntimeError("refusing worktrees outside the demo workspace") from error
    before = (
        _git(repository, "rev-parse", "HEAD"),
        _git(repository, "branch", "--show-current"),
        _git(repository, "status", "--porcelain=v1"),
    )
    os.environ["BISECTION_WORK_ROOT"] = str(worktree_root)
    get_settings.cache_clear()
    seeded = seed_demo(api, runner)
    definition = api.request(
        "GET", f"/api/v1/evaluation-definitions/{seeded['ids']['evaluation_definition']}"
    )
    summary = {"created": 0, "reused": 0}
    run, _ = _seed_experiment(
        api,
        definition,
        summary,
        runner,
        name=f"{DEMO} — Git bisection evidence",
        baseline_sha=commits[0],
        candidate_sha=commits[-1],
    )
    check = _seed_regression(api, run, summary)
    sessions = api.request(
        "GET", f"/api/v1/bisection-sessions?regression_check_id={check['id']}&page_size=100"
    )["items"]
    if sessions:
        session = api.request("GET", f"/api/v1/bisection-sessions/{sessions[0]['id']}")
    else:
        session = api.request(
            "POST",
            "/api/v1/bisection-sessions",
            {
                "regression_check_id": check["id"],
                "repository_path": str(repository),
            },
        )
    analyses = api.request(
        "GET", f"/api/v1/bisection-sessions/{session['id']}/analyses?page_size=100"
    )["items"]
    analysis = (
        analyses[0]
        if analyses
        else api.request(
            "POST",
            f"/api/v1/bisection-sessions/{session['id']}/analyses",
            {
                "configuration": {
                    "executable": sys.executable,
                    "args": [
                        "-c",
                        "from pathlib import Path; "
                        "v=int(Path('version.txt').read_text()); "
                        "raise SystemExit(0 if v < 3 else 1)",
                    ],
                    "timeout_seconds": 10,
                }
            },
        )
    )
    if analysis["status"] == "pending":
        runner(_start, UUID(analysis["id"]))
        runner(_drive, UUID(analysis["id"]))
        analysis = api.request("GET", f"/api/v1/bisection-analyses/{analysis['id']}")
    after = (
        _git(repository, "rev-parse", "HEAD"),
        _git(repository, "branch", "--show-current"),
        _git(repository, "status", "--porcelain=v1"),
    )
    if before != after:
        raise RuntimeError("demo target repository was mutated")
    if (
        analysis["status"] != "attributed"
        or analysis["final_good_commit_sha"] != commits[2]
        or analysis["final_bad_commit_sha"] != commits[3]
    ):
        raise RuntimeError("demo bisection did not identify the expected boundary")
    return {
        "workspace": str(workspace),
        "repository": str(repository),
        "session_id": session["id"],
        "analysis_id": analysis["id"],
        "previous_good": commits[2],
        "first_regressed": commits[3],
        "target_unchanged": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(tempfile.gettempdir()) / "agentscope-demo-bisection-v1",
    )
    args = parser.parse_args()
    try:
        result = run_bisection_demo(Api(args.api_url), args.workspace)
    except Exception as error:
        raise SystemExit(
            f"Bisection demo failed safely ({type(error).__name__}); no target was reset."
        ) from None
    print("AgentScope Demo bisection attributed the deterministic boundary:")
    print(f"  previous good: {result['previous_good']}")
    print(f"  first regressed: {result['first_regressed']}")
    print(f"  session: /regressions/bisections/{result['session_id']}")
    print(f"  target repository unchanged: {result['target_unchanged']}")


if __name__ == "__main__":
    main()
