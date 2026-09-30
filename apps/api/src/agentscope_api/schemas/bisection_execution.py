from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ProbeConfiguration(ContractModel):
    executable: Annotated[str, Field(min_length=1, max_length=500)]
    args: Annotated[list[Annotated[str, Field(max_length=1_000)]], Field(max_length=64)] = Field(
        default_factory=list
    )
    working_directory: Annotated[str, Field(min_length=1, max_length=500)] = "."
    timeout_seconds: Annotated[int, Field(ge=1, le=3_600)] = 300
    max_stdout_bytes: Annotated[int, Field(ge=1, le=1_048_576)] = 65_536
    max_stderr_bytes: Annotated[int, Field(ge=1, le=1_048_576)] = 65_536

    @field_validator("executable")
    @classmethod
    def executable_is_one_argv_value(cls, value: str) -> str:
        if not value.strip() or "\x00" in value or any(char in value for char in "\r\n"):
            raise ValueError("executable must be one non-empty argv value")
        return value

    @field_validator("args")
    @classmethod
    def arguments_have_no_nul(cls, values: list[str]) -> list[str]:
        if any("\x00" in value for value in values):
            raise ValueError("arguments cannot contain NUL")
        return values

    @field_validator("working_directory")
    @classmethod
    def working_directory_is_relative_and_contained(cls, value: str) -> str:
        windows = PureWindowsPath(value)
        posix = PurePosixPath(value)
        if (
            windows.is_absolute()
            or posix.is_absolute()
            or ".." in windows.parts
            or ".." in posix.parts
            or "\x00" in value
        ):
            raise ValueError("working directory must remain beneath the repository root")
        return value


class BisectionExecutionRunCreate(ContractModel):
    configuration: ProbeConfiguration


class BisectionExecutionRunSubmit(ContractModel):
    commit_shas: Annotated[list[str], Field(min_length=1, max_length=50)]

    @field_validator("commit_shas")
    @classmethod
    def commits_are_unique_full_shas(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(
            len(value) != 40 or any(char not in "0123456789abcdef" for char in value)
            for value in values
        ):
            raise ValueError("commit SHAs must be unique lowercase full SHAs")
        return values


class BisectionExecutionRunStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BisectionExecutionTargetStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    EXECUTION_FAILED = "execution_failed"


class BisectionExecutionOutcome(StrEnum):
    PASS = "pass"
    REGRESSION = "regression"
    INDETERMINATE = "indeterminate"
    EXECUTION_FAILED = "execution_failed"


class BisectionExecutionAttempt(ContractModel):
    id: UUID
    run_id: UUID
    commit_sha: str
    attempt_number: int
    status: Literal["running", "completed", "failed"]
    outcome: BisectionExecutionOutcome | None
    started_at: datetime
    finished_at: datetime | None
    exit_code: int | None
    duration_ms: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    failure_kind: str | None
    failure_message: str | None
    cleanup_failed: bool
    cleanup_message: str | None


class BisectionExecutionTarget(ContractModel):
    commit_sha: str
    commit_position: int
    status: BisectionExecutionTargetStatus
    outcome: BisectionExecutionOutcome | None
    attempt_count: int
    started_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    duration_ms: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    failure_kind: str | None
    failure_message: str | None
    cleanup_failed: bool
    cleanup_message: str | None
    attempts: list[BisectionExecutionAttempt] = Field(default_factory=list)


class BisectionExecutionRun(ContractModel):
    id: UUID
    session_id: UUID
    status: BisectionExecutionRunStatus
    configuration_schema_version: Literal["1"]
    configuration: ProbeConfiguration
    repository_fingerprint: str
    repository_root: str
    planned_commit_count: int
    requested_commit_count: int
    completed_valid_count: int
    regression_count: int
    indeterminate_count: int
    execution_failure_count: int
    remaining_count: int
    error_category: str | None
    error_message: str | None
    created_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    targets: list[BisectionExecutionTarget] = Field(default_factory=list)


class BisectionExecutionRunListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0
    status: BisectionExecutionRunStatus | None = None


class BisectionExecutionRunList(ContractModel):
    items: list[BisectionExecutionRun]
    has_more: bool


class BisectionExecutionAccepted(ContractModel):
    run_id: UUID
    status: Literal["queued"] = "queued"
    queue_delivery: Literal["enqueued", "deferred"]
