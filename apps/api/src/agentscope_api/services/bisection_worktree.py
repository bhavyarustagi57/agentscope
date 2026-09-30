from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import UUID

from agentscope_api.schemas.bisection_execution import ProbeConfiguration

ROOT_MARKER = ".agentscope-bisection-root-v1"
ATTEMPT_MARKER = ".agentscope-owner.json"
GIT_OUTPUT_LIMIT = 1_048_576


@dataclass(frozen=True, slots=True)
class ProbeResult:
    exit_code: int | None
    duration_ms: int
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    failure_kind: str | None = None
    failure_message: str | None = None
    cleanup_failed: bool = False
    cleanup_message: str | None = None


def classify_exit_code(exit_code: int) -> str:
    return {0: "pass", 1: "regression"}.get(exit_code, "indeterminate")


def _bounded_text(stream: Any, limit: int) -> tuple[str, bool]:
    size = stream.tell()
    stream.seek(0)
    raw = stream.read(limit)
    text = raw.decode("utf-8", errors="replace")
    while len(text.encode("utf-8")) > limit:
        text = text[:-1]
    return text, size > limit


def _minimal_environment() -> dict[str, str]:
    allowed = (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
    )
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update({"GIT_TERMINAL_PROMPT": "0", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    return environment


class WorktreeProbeRunner:
    def _git(self, repository: Path, args: list[str], *, timeout: int = 30) -> None:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                result = subprocess.run(
                    ["git", *args],
                    cwd=repository,
                    check=False,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout,
                    shell=False,
                    env=_minimal_environment(),
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RuntimeError("Git worktree operation failed") from error
            if (
                result.returncode
                or stdout.tell() > GIT_OUTPUT_LIMIT
                or stderr.tell() > GIT_OUTPUT_LIMIT
            ):
                raise RuntimeError("Git worktree operation failed")

    def _owned_attempt_directory(
        self, work_root: Path, run_id: UUID, commit_sha: str, token: UUID
    ) -> tuple[Path, dict[str, str]]:
        root = work_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        root_marker = root / ROOT_MARKER
        if root_marker.exists():
            if root_marker.read_text(encoding="utf-8") != "AgentScope bisection work root v1\n":
                raise RuntimeError("work root ownership marker is invalid")
        else:
            if any(root.iterdir()):
                raise RuntimeError("work root is not empty or AgentScope-owned")
            root_marker.write_text("AgentScope bisection work root v1\n", encoding="utf-8")
        attempt = (root / run_id.hex[:12] / commit_sha[:12] / token.hex[:12]).resolve()
        if root not in attempt.parents or attempt.exists():
            raise RuntimeError("worktree destination is unsafe")
        attempt.mkdir(parents=True)
        ownership = {"run_id": str(run_id), "commit_sha": commit_sha, "token": str(token)}
        (attempt / ATTEMPT_MARKER).write_text(json.dumps(ownership), encoding="utf-8")
        return attempt, ownership

    def _cleanup(
        self,
        repository: Path,
        work_root: Path,
        attempt: Path,
        ownership: dict[str, str],
        *,
        worktree_added: bool,
    ) -> tuple[bool, str | None]:
        try:
            root = work_root.resolve(strict=True)
            resolved_attempt = attempt.resolve(strict=True)
            if (
                root not in resolved_attempt.parents
                or len(resolved_attempt.relative_to(root).parts) != 3
            ):
                raise RuntimeError("owned worktree path failed containment validation")
            marker = resolved_attempt / ATTEMPT_MARKER
            if json.loads(marker.read_text(encoding="utf-8")) != ownership:
                raise RuntimeError("owned worktree marker mismatch")
            worktree = resolved_attempt / "repo"
            if worktree_added:
                self._git(repository, ["worktree", "remove", "--force", str(worktree)])
            elif worktree.exists():
                worktree.rmdir()
            marker.unlink()
            resolved_attempt.rmdir()
            for parent in (resolved_attempt.parent, resolved_attempt.parent.parent):
                try:
                    parent.rmdir()
                except OSError:
                    break
            return False, None
        except (OSError, ValueError, json.JSONDecodeError, RuntimeError):
            return True, "AgentScope-owned worktree cleanup failed"

    def execute(
        self,
        *,
        repository_root: str,
        work_root: str,
        run_id: UUID,
        commit_sha: str,
        token: UUID,
        configuration: ProbeConfiguration,
    ) -> ProbeResult:
        repository = Path(repository_root).resolve(strict=True)
        root = Path(work_root)
        attempt: Path | None = None
        ownership: dict[str, str] = {}
        worktree_added = False
        started = time.monotonic()
        result = ProbeResult(exit_code=None, duration_ms=0)
        try:
            attempt, ownership = self._owned_attempt_directory(root, run_id, commit_sha, token)
            worktree = attempt / "repo"
            self._git(repository, ["worktree", "add", "--detach", str(worktree), commit_sha])
            worktree_added = True
            cwd = (worktree / configuration.working_directory).resolve(strict=True)
            resolved_worktree = worktree.resolve(strict=True)
            if (
                cwd != resolved_worktree and resolved_worktree not in cwd.parents
            ) or not cwd.is_dir():
                raise ValueError("working directory is outside the isolated worktree")
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                try:
                    completed = subprocess.run(
                        [configuration.executable, *configuration.args],
                        cwd=cwd,
                        check=False,
                        stdout=stdout,
                        stderr=stderr,
                        timeout=configuration.timeout_seconds,
                        shell=False,
                        env=_minimal_environment(),
                    )
                    output, stdout_truncated = _bounded_text(stdout, configuration.max_stdout_bytes)
                    error_output, stderr_truncated = _bounded_text(
                        stderr, configuration.max_stderr_bytes
                    )
                    result = ProbeResult(
                        exit_code=completed.returncode,
                        duration_ms=int((time.monotonic() - started) * 1_000),
                        stdout=output,
                        stderr=error_output,
                        stdout_truncated=stdout_truncated,
                        stderr_truncated=stderr_truncated,
                    )
                except subprocess.TimeoutExpired:
                    output, stdout_truncated = _bounded_text(stdout, configuration.max_stdout_bytes)
                    error_output, stderr_truncated = _bounded_text(
                        stderr, configuration.max_stderr_bytes
                    )
                    result = ProbeResult(
                        exit_code=None,
                        duration_ms=int((time.monotonic() - started) * 1_000),
                        stdout=output,
                        stderr=error_output,
                        stdout_truncated=stdout_truncated,
                        stderr_truncated=stderr_truncated,
                        timed_out=True,
                        failure_kind="timeout",
                        failure_message="probe execution timed out",
                    )
                except OSError:
                    result = ProbeResult(
                        exit_code=None,
                        duration_ms=int((time.monotonic() - started) * 1_000),
                        failure_kind="spawn_failed",
                        failure_message="probe executable could not be started",
                    )
        except (OSError, ValueError, RuntimeError):
            result = ProbeResult(
                exit_code=None,
                duration_ms=int((time.monotonic() - started) * 1_000),
                failure_kind="worktree_failed",
                failure_message="isolated worktree setup failed",
            )
        finally:
            if attempt is not None:
                cleanup_failed, cleanup_message = self._cleanup(
                    repository,
                    root,
                    attempt,
                    ownership,
                    worktree_added=worktree_added,
                )
                result = replace(
                    result,
                    cleanup_failed=cleanup_failed,
                    cleanup_message=cleanup_message,
                )
        return result
