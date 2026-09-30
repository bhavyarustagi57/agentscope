from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from agentscope_api.database import Base


class BisectionSessionRecord(Base):
    __tablename__ = "bisection_sessions"
    __table_args__ = (
        UniqueConstraint(
            "regression_check_id",
            "repository_fingerprint",
            "baseline_commit_sha",
            "candidate_commit_sha",
            name="uq_bisection_sessions_plan",
        ),
        CheckConstraint("status = 'ready'", name="ck_bisection_sessions_status"),
        CheckConstraint(
            "baseline_commit_sha ~ '^[0-9a-f]{40}$' "
            "AND candidate_commit_sha ~ '^[0-9a-f]{40}$' "
            "AND repository_head_sha ~ '^[0-9a-f]{40}$' "
            "AND baseline_commit_sha <> candidate_commit_sha",
            name="ck_bisection_sessions_shas",
        ),
        CheckConstraint(
            "repository_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_bisection_sessions_fingerprint",
        ),
        CheckConstraint(
            "commit_count >= 1 AND commit_count <= maximum_commit_count "
            "AND maximum_commit_count >= 1 AND maximum_commit_count <= 5000",
            name="ck_bisection_sessions_commit_count",
        ),
        CheckConstraint(
            "jsonb_typeof(baseline_provenance) = 'object' "
            "AND jsonb_typeof(candidate_provenance) = 'object'",
            name="ck_bisection_sessions_provenance",
        ),
        Index("ix_bisection_sessions_created_id", "created_at", "id"),
        Index("ix_bisection_sessions_regression_check", "regression_check_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    regression_check_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("regression_checks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    repository_path: Mapped[str] = mapped_column(String(2_048), nullable=False)
    repository_root: Mapped[str] = mapped_column(String(2_048), nullable=False)
    repository_common_dir: Mapped[str] = mapped_column(String(2_048), nullable=False)
    repository_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    repository_head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    baseline_revision: Mapped[str] = mapped_column(String(200), nullable=False)
    candidate_revision: Mapped[str] = mapped_column(String(200), nullable=False)
    baseline_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    candidate_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    baseline_provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    candidate_provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    commit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    maximum_commit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BisectionCommitRecord(Base):
    __tablename__ = "bisection_commits"
    __table_args__ = (
        UniqueConstraint("session_id", "commit_sha", name="uq_bisection_commits_sha"),
        CheckConstraint(
            "position >= 0 AND position < 5000",
            name="ck_bisection_commits_position",
        ),
        CheckConstraint(
            "commit_sha ~ '^[0-9a-f]{40}$'",
            name="ck_bisection_commits_sha",
        ),
        CheckConstraint(
            "jsonb_typeof(parent_shas) = 'array'",
            name="ck_bisection_commits_parents",
        ),
        Index("ix_bisection_commits_sha", "commit_sha"),
    )

    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bisection_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    parent_shas: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
