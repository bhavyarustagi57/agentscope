from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from agentscope_api.database import Base


class BisectionAnalysisRecord(Base):
    __tablename__ = "bisection_analyses"
    __table_args__ = (
        UniqueConstraint("id", "session_id", name="uq_bisection_analyses_session"),
        CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'waiting', "
            "'attributed', 'inconclusive', 'failed')",
            name="ck_bisection_analyses_status",
        ),
        CheckConstraint(
            "configuration_schema_version = '1' AND jsonb_typeof(configuration) = 'object'",
            name="ck_bisection_analyses_configuration",
        ),
        CheckConstraint(
            "baseline_commit_sha ~ '^[0-9a-f]{40}$' AND candidate_commit_sha ~ '^[0-9a-f]{40}$' "
            "AND good_commit_sha ~ '^[0-9a-f]{40}$' AND bad_commit_sha ~ '^[0-9a-f]{40}$'",
            name="ck_bisection_analyses_shas",
        ),
        CheckConstraint(
            "total_commit_count >= 1 AND total_commit_count <= 5000 "
            "AND good_position >= -1 AND bad_position >= 0 "
            "AND good_position < bad_position AND bad_position < total_commit_count",
            name="ck_bisection_analyses_interval",
        ),
        CheckConstraint(
            "step_count >= 0 AND reused_evidence_count >= 0 AND new_evidence_count >= 0 "
            "AND indeterminate_count >= 0 AND execution_failure_count >= 0",
            name="ck_bisection_analyses_counts",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_bisection_analyses_lease",
        ),
        CheckConstraint(
            "(status = 'waiting' AND waiting_commit_sha IS NOT NULL "
            "AND waiting_position IS NOT NULL AND waiting_execution_run_id IS NOT NULL) OR "
            "(status <> 'waiting')",
            name="ck_bisection_analyses_waiting",
        ),
        CheckConstraint(
            "(status = 'attributed' AND completed_at IS NOT NULL "
            "AND final_good_commit_sha IS NOT NULL AND final_bad_commit_sha IS NOT NULL "
            "AND final_good_snapshot IS NOT NULL AND final_bad_snapshot IS NOT NULL "
            "AND terminal_reason IS NULL) OR "
            "(status IN ('inconclusive', 'failed') AND completed_at IS NOT NULL "
            "AND terminal_reason IS NOT NULL) OR "
            "(status IN ('pending', 'queued', 'running', 'waiting') AND completed_at IS NULL "
            "AND final_good_commit_sha IS NULL AND final_bad_commit_sha IS NULL)",
            name="ck_bisection_analyses_terminal",
        ),
        Index("ix_bisection_analyses_created", "created_at", "id"),
        Index("ix_bisection_analyses_session_created", "session_id", "created_at", "id"),
        Index("ix_bisection_analyses_recovery", "status", "last_enqueued_at", "lease_expires_at"),
        Index(
            "uq_bisection_analyses_one_active",
            "session_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'queued', 'running', 'waiting')"),
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
    baseline_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    candidate_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    total_commit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    good_position: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    good_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    bad_position: Mapped[int] = mapped_column(Integer, nullable=False)
    bad_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    waiting_position: Mapped[int | None] = mapped_column(Integer)
    waiting_commit_sha: Mapped[str | None] = mapped_column(String(40))
    waiting_execution_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bisection_execution_runs.id", ondelete="RESTRICT")
    )
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reused_evidence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    new_evidence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    indeterminate_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    execution_failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    final_good_commit_sha: Mapped[str | None] = mapped_column(String(40))
    final_good_position: Mapped[int | None] = mapped_column(Integer)
    final_bad_commit_sha: Mapped[str | None] = mapped_column(String(40))
    final_bad_position: Mapped[int | None] = mapped_column(Integer)
    final_good_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    final_bad_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    terminal_reason: Mapped[str | None] = mapped_column(String(60))
    terminal_message: Mapped[str | None] = mapped_column(String(1_000))
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BisectionAnalysisStepRecord(Base):
    __tablename__ = "bisection_analysis_steps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analysis_id", "session_id"],
            ["bisection_analyses.id", "bisection_analyses.session_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["session_id", "selected_commit_sha"],
            ["bisection_commits.session_id", "bisection_commits.commit_sha"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "analysis_id", "selected_position", name="uq_bisection_analysis_steps_position"
        ),
        CheckConstraint(
            "sequence_number >= 1 AND sequence_number <= 5001",
            name="ck_bisection_analysis_steps_sequence",
        ),
        CheckConstraint(
            "evidence_source IN ('reused', 'requested')",
            name="ck_bisection_analysis_steps_source",
        ),
        CheckConstraint(
            "observed_outcome IN ('pass', 'regression', 'indeterminate', 'execution_failed')",
            name="ck_bisection_analysis_steps_outcome",
        ),
        CheckConstraint(
            "decision IN ('confirm_candidate', 'advance_good', 'retreat_bad', "
            "'skip_indeterminate', 'skip_execution_failed')",
            name="ck_bisection_analysis_steps_decision",
        ),
        CheckConstraint(
            "good_position_before >= -1 AND bad_position_before >= 0 "
            "AND good_position_before < bad_position_before "
            "AND selected_position >= 0 AND good_position_after >= -1 "
            "AND bad_position_after >= 0 AND good_position_after < bad_position_after",
            name="ck_bisection_analysis_steps_positions",
        ),
        Index("ix_bisection_analysis_steps_selected", "session_id", "selected_commit_sha"),
    )

    analysis_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    sequence_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    good_position_before: Mapped[int] = mapped_column(Integer, nullable=False)
    good_commit_sha_before: Mapped[str] = mapped_column(String(40), nullable=False)
    bad_position_before: Mapped[int] = mapped_column(Integer, nullable=False)
    bad_commit_sha_before: Mapped[str] = mapped_column(String(40), nullable=False)
    selected_position: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_source: Mapped[str] = mapped_column(String(20), nullable=False)
    execution_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bisection_execution_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    observed_outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    good_position_after: Mapped[int] = mapped_column(Integer, nullable=False)
    good_commit_sha_after: Mapped[str] = mapped_column(String(40), nullable=False)
    bad_position_after: Mapped[int] = mapped_column(Integer, nullable=False)
    bad_commit_sha_after: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
