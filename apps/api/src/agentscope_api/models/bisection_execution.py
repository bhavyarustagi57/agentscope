from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from agentscope_api.database import Base


class BisectionExecutionRunRecord(Base):
    __tablename__ = "bisection_execution_runs"
    __table_args__ = (
        UniqueConstraint("id", "session_id", name="uq_bisection_execution_runs_session"),
        CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'completed', 'failed')",
            name="ck_bisection_execution_runs_status",
        ),
        CheckConstraint(
            "configuration_schema_version = '1' AND jsonb_typeof(configuration) = 'object'",
            name="ck_bisection_execution_runs_configuration",
        ),
        CheckConstraint(
            "repository_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_bisection_execution_runs_repository",
        ),
        CheckConstraint(
            "planned_commit_count >= 1 AND planned_commit_count <= 5000 "
            "AND requested_commit_count >= 0 AND requested_commit_count <= 50",
            name="ck_bisection_execution_runs_counts",
        ),
        CheckConstraint(
            "(status = 'pending' AND requested_commit_count = 0 AND queued_at IS NULL "
            "AND completed_at IS NULL AND error_category IS NULL) OR "
            "(status IN ('queued', 'running') AND requested_commit_count >= 1 "
            "AND queued_at IS NOT NULL AND completed_at IS NULL AND error_category IS NULL) OR "
            "(status = 'completed' AND requested_commit_count >= 1 "
            "AND completed_at IS NOT NULL AND error_category IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND error_category IS NOT NULL)",
            name="ck_bisection_execution_runs_lifecycle",
        ),
        Index("ix_bisection_execution_runs_created", "created_at", "id"),
        Index("ix_bisection_execution_runs_session_created", "session_id", "created_at", "id"),
        Index("ix_bisection_execution_runs_recovery", "status", "last_enqueued_at"),
        Index(
            "uq_bisection_execution_runs_one_active",
            "session_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'queued', 'running')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bisection_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    configuration_schema_version: Mapped[str] = mapped_column(
        String(20), nullable=False, default="1", server_default="1"
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    repository_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    repository_root: Mapped[str] = mapped_column(String(2_048), nullable=False)
    planned_commit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_commit_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_category: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(String(4_000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BisectionExecutionTargetRecord(Base):
    __tablename__ = "bisection_execution_targets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "session_id"],
            ["bisection_execution_runs.id", "bisection_execution_runs.session_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["session_id", "commit_sha"],
            ["bisection_commits.session_id", "bisection_commits.commit_sha"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'execution_failed')",
            name="ck_bisection_execution_targets_status",
        ),
        CheckConstraint(
            "commit_sha ~ '^[0-9a-f]{40}$' AND commit_position >= 0 AND commit_position < 5000",
            name="ck_bisection_execution_targets_commit",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 3",
            name="ck_bisection_execution_targets_attempts",
        ),
        CheckConstraint(
            "(status = 'queued' AND outcome IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL AND finished_at IS NULL) OR "
            "(status = 'running' AND outcome IS NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND started_at IS NOT NULL AND finished_at IS NULL) OR "
            "(status = 'completed' AND outcome IN ('pass', 'regression', 'indeterminate') "
            "AND lease_token IS NULL AND lease_expires_at IS NULL AND finished_at IS NOT NULL) OR "
            "(status = 'execution_failed' AND outcome = 'execution_failed' "
            "AND lease_token IS NULL AND lease_expires_at IS NULL AND finished_at IS NOT NULL "
            "AND failure_kind IS NOT NULL)",
            name="ck_bisection_execution_targets_lifecycle",
        ),
        CheckConstraint(
            "octet_length(stdout) <= 1048576 AND octet_length(stderr) <= 1048576",
            name="ck_bisection_execution_targets_output",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_bisection_execution_targets_duration",
        ),
        Index("ix_bisection_execution_targets_recovery", "status", "lease_expires_at"),
        Index("ix_bisection_execution_targets_run_position", "run_id", "commit_position"),
    )

    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    commit_sha: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    commit_position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    outcome: Mapped[str | None] = mapped_column(String(24))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    stdout: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    stderr: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    stdout_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    stderr_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    timed_out: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    failure_kind: Mapped[str | None] = mapped_column(String(60))
    failure_message: Mapped[str | None] = mapped_column(String(1_000))
    cleanup_failed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    cleanup_message: Mapped[str | None] = mapped_column(String(1_000))


class BisectionExecutionAttemptRecord(Base):
    __tablename__ = "bisection_execution_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "commit_sha"],
            ["bisection_execution_targets.run_id", "bisection_execution_targets.commit_sha"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "run_id", "commit_sha", "attempt_number", name="uq_bisection_execution_attempts_number"
        ),
        UniqueConstraint("lease_token", name="uq_bisection_execution_attempts_lease"),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_bisection_execution_attempts_status",
        ),
        CheckConstraint(
            "attempt_number >= 1 AND attempt_number <= 3",
            name="ck_bisection_execution_attempts_number",
        ),
        CheckConstraint(
            "(status = 'running' AND outcome IS NULL AND finished_at IS NULL) OR "
            "(status = 'completed' AND outcome IN ('pass', 'regression', 'indeterminate') "
            "AND finished_at IS NOT NULL AND failure_kind IS NULL) OR "
            "(status = 'failed' AND outcome = 'execution_failed' "
            "AND finished_at IS NOT NULL AND failure_kind IS NOT NULL)",
            name="ck_bisection_execution_attempts_lifecycle",
        ),
        CheckConstraint(
            "octet_length(stdout) <= 1048576 AND octet_length(stderr) <= 1048576",
            name="ck_bisection_execution_attempts_output",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_bisection_attempts_duration"
        ),
        Index("ix_bisection_execution_attempts_target", "run_id", "commit_sha", "attempt_number"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_token: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    outcome: Mapped[str | None] = mapped_column(String(24))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    stdout: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    stderr: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    stdout_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stderr_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timed_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failure_kind: Mapped[str | None] = mapped_column(String(60))
    failure_message: Mapped[str | None] = mapped_column(String(1_000))
    cleanup_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cleanup_message: Mapped[str | None] = mapped_column(String(1_000))
