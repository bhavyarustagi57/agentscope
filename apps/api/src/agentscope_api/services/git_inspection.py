from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

MAX_GIT_OUTPUT_BYTES = 1_048_576
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class GitInspectionError(Exception):
    pass


class GitRepositoryNotFound(GitInspectionError):
    pass


class NotGitRepository(GitInspectionError):
    pass


class GitCommandTimeout(GitInspectionError):
    pass


class GitCommandFailed(GitInspectionError):
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


class MalformedGitOutput(GitInspectionError):
    pass


class UnsafeRepositoryState(GitInspectionError):
    pass


class RevisionNotFound(GitInspectionError):
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint


class BisectionEndpointsIdentical(GitInspectionError):
    pass


class BaselineNotAncestor(GitInspectionError):
    pass


class CommitRangeTooLarge(GitInspectionError):
    pass


@dataclass(frozen=True)
class GitRepositorySnapshot:
    path: str
    root: str
    common_dir: str
    fingerprint: str
    head_sha: str


@dataclass(frozen=True)
class GitCommitSnapshot:
    position: int
    commit_sha: str
    committed_at: datetime
    subject: str
    parent_shas: list[str]


@dataclass(frozen=True)
class GitBisectionPlan:
    repository: GitRepositorySnapshot
    baseline_sha: str
    candidate_sha: str
    commits: list[GitCommitSnapshot]


class _CommitMetadata(TypedDict):
    commit_sha: str
    committed_at: datetime
    subject: str
    parent_shas: list[str]


class GitInspector:
    def __init__(self, *, timeout_seconds: int = 5) -> None:
        self.timeout_seconds = timeout_seconds

    def _run(self, repository: Path, args: list[str]) -> str:
        try:
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                result = subprocess.run(
                    ["git", *args],
                    cwd=repository,
                    check=False,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=self.timeout_seconds,
                    shell=False,
                )
                if (
                    stdout_file.tell() > MAX_GIT_OUTPUT_BYTES
                    or stderr_file.tell() > MAX_GIT_OUTPUT_BYTES
                ):
                    raise MalformedGitOutput
                stdout_file.seek(0)
                output = stdout_file.read(MAX_GIT_OUTPUT_BYTES)
        except subprocess.TimeoutExpired as error:
            raise GitCommandTimeout from error
        except (OSError, ValueError) as error:
            raise GitInspectionError from error
        if result.returncode:
            raise GitCommandFailed(result.returncode)
        try:
            return output.decode("utf-8", errors="strict").rstrip("\r\n")
        except UnicodeDecodeError as error:
            raise MalformedGitOutput from error

    def inspect(
        self,
        repository_path: str,
        baseline_revision: str,
        candidate_revision: str,
        *,
        maximum_commit_count: int,
    ) -> GitBisectionPlan:
        try:
            path = Path(repository_path).resolve(strict=True)
        except OSError as error:
            raise GitRepositoryNotFound from error
        if not path.is_dir():
            raise GitRepositoryNotFound
        try:
            root = Path(
                self._run(path, ["rev-parse", "--path-format=absolute", "--show-toplevel"])
            ).resolve(strict=True)
            common_dir = Path(
                self._run(path, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
            ).resolve(strict=True)
        except GitCommandFailed as error:
            raise NotGitRepository from error
        except OSError as error:
            raise MalformedGitOutput from error
        if max(len(str(path)), len(str(root)), len(str(common_dir))) > 2_048:
            raise MalformedGitOutput
        shallow = self._run(path, ["rev-parse", "--is-shallow-repository"])
        if shallow not in {"true", "false"}:
            raise MalformedGitOutput
        if shallow == "true":
            raise UnsafeRepositoryState

        baseline_sha = self._resolve(path, baseline_revision, "baseline")
        candidate_sha = self._resolve(path, candidate_revision, "candidate")
        if baseline_sha == candidate_sha:
            raise BisectionEndpointsIdentical
        try:
            self._run(path, ["merge-base", "--is-ancestor", baseline_sha, candidate_sha])
        except GitCommandFailed as error:
            if error.returncode == 1:
                raise BaselineNotAncestor from error
            raise

        commit_output = self._run(
            path,
            [
                "rev-list",
                "--ancestry-path",
                "--topo-order",
                "--reverse",
                f"--max-count={maximum_commit_count + 1}",
                f"{baseline_sha}..{candidate_sha}",
                "--",
            ],
        )
        commit_shas = commit_output.splitlines()
        if len(commit_shas) > maximum_commit_count:
            raise CommitRangeTooLarge
        if not commit_shas or commit_shas[-1] != candidate_sha:
            raise MalformedGitOutput
        if len(set(commit_shas)) != len(commit_shas) or any(
            not _FULL_SHA.fullmatch(sha) for sha in commit_shas
        ):
            raise MalformedGitOutput

        metadata = self._metadata(path, commit_shas)
        head_sha = self._resolve(path, "HEAD", "head")
        fingerprint = hashlib.sha256(os.path.normcase(str(common_dir)).encode("utf-8")).hexdigest()
        return GitBisectionPlan(
            repository=GitRepositorySnapshot(
                path=str(path),
                root=str(root),
                common_dir=str(common_dir),
                fingerprint=fingerprint,
                head_sha=head_sha,
            ),
            baseline_sha=baseline_sha,
            candidate_sha=candidate_sha,
            commits=[
                GitCommitSnapshot(position=position, **metadata[sha])
                for position, sha in enumerate(commit_shas)
            ],
        )

    def _resolve(self, repository: Path, revision: str, endpoint: str) -> str:
        try:
            sha = self._run(
                repository,
                ["rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
            )
        except GitCommandFailed as error:
            raise RevisionNotFound(endpoint) from error
        if not _FULL_SHA.fullmatch(sha):
            raise MalformedGitOutput
        return sha

    def _metadata(self, repository: Path, commit_shas: list[str]) -> dict[str, _CommitMetadata]:
        output = self._run(
            repository,
            [
                "show",
                "--no-patch",
                "--no-show-signature",
                "--format=%H%x1f%ct%x1f%P%x1f%s%x1e",
                *commit_shas,
                "--",
            ],
        )
        metadata: dict[str, _CommitMetadata] = {}
        for raw_record in output.split("\x1e"):
            record = raw_record.strip("\r\n")
            if not record:
                continue
            fields = record.split("\x1f")
            if len(fields) != 4 or not _FULL_SHA.fullmatch(fields[0]):
                raise MalformedGitOutput
            try:
                committed_at = datetime.fromtimestamp(int(fields[1]), UTC)
            except (OverflowError, ValueError) as error:
                raise MalformedGitOutput from error
            parents = fields[2].split() if fields[2] else []
            if any(not _FULL_SHA.fullmatch(parent) for parent in parents):
                raise MalformedGitOutput
            metadata[fields[0]] = {
                "commit_sha": fields[0],
                "committed_at": committed_at,
                "subject": fields[3][:500],
                "parent_shas": parents,
            }
        if set(metadata) != set(commit_shas):
            raise MalformedGitOutput
        return metadata
