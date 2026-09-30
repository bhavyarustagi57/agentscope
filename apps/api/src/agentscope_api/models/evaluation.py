from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
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


class EvaluationDefinitionRecord(Base):
    __tablename__ = "evaluation_definitions"
    __table_args__ = (
        CheckConstraint(
            "evaluator_kind IN ('exact_match', 'contains', 'numeric_threshold')",
            name="ck_evaluation_definitions_kind",
        ),
        CheckConstraint(
            "jsonb_typeof(evaluator_config) = 'object'",
            name="ck_evaluation_definitions_config_object",
        ),
        Index("ix_evaluation_definitions_created_id", "created_at", "id"),
        Index("ix_evaluation_definitions_kind", "evaluator_kind"),
        Index("ix_evaluation_definitions_enabled", "is_enabled"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2_000))
    evaluator_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    evaluator_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EvaluationRunRecord(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'completed', 'failed')",
            name="ck_evaluation_runs_status",
        ),
        CheckConstraint(
            "evaluator_kind IN ('exact_match', 'contains', 'numeric_threshold')",
            name="ck_evaluation_runs_kind",
        ),
        CheckConstraint(
            "jsonb_typeof(evaluator_config) = 'object'",
            name="ck_evaluation_runs_config_object",
        ),
        CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="ck_evaluation_runs_start_order",
        ),
        CheckConstraint(
            "completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at)",
            name="ck_evaluation_runs_completion_order",
        ),
        CheckConstraint(
            "(status IN ('pending', 'queued') AND started_at IS NULL AND completed_at IS NULL "
            "AND error_message IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND error_message IS NULL) OR "
            "(status = 'completed' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND error_message IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL)",
            name="ck_evaluation_runs_lifecycle",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 4",
            name="ck_evaluation_runs_attempt_count",
        ),
        CheckConstraint(
            "(queued_at IS NULL OR queued_at >= created_at) AND "
            "(last_enqueued_at IS NULL OR "
            "(queued_at IS NOT NULL AND last_enqueued_at >= queued_at))",
            name="ck_evaluation_runs_queue_time_order",
        ),
        Index("ix_evaluation_runs_created_id", "created_at", "id"),
        Index("ix_evaluation_runs_definition_created", "definition_id", "created_at", "id"),
        Index("ix_evaluation_runs_status_created", "status", "created_at", "id"),
        Index("ix_evaluation_runs_recovery", "status", "last_enqueued_at", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    definition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("evaluation_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    definition_name: Mapped[str] = mapped_column(String(200), nullable=False)
    evaluator_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    evaluator_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    error_message: Mapped[str | None] = mapped_column(String(4_000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class EvaluationRunSubjectRecord(Base):
    __tablename__ = "evaluation_run_subjects"
    __table_args__ = (
        UniqueConstraint("run_id", "position", name="uq_evaluation_run_subjects_position"),
        CheckConstraint(
            "position >= 0 AND position < 1000", name="ck_evaluation_run_subjects_position"
        ),
        Index("ix_evaluation_run_subjects_trace_id", "trace_id"),
    )

    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    trace_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("traces.trace_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EvaluationResultRecord(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("run_id", "trace_id", name="uq_evaluation_results_run_trace"),
        CheckConstraint(
            "outcome IN ('passed', 'failed', 'error')",
            name="ck_evaluation_results_outcome",
        ),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="ck_evaluation_results_score",
        ),
        CheckConstraint(
            "outcome <> 'error' OR score IS NULL",
            name="ck_evaluation_results_error_score",
        ),
        CheckConstraint(
            "jsonb_typeof(details) = 'object'",
            name="ck_evaluation_results_details_object",
        ),
        Index("ix_evaluation_results_run_created", "run_id", "created_at", "id"),
        Index("ix_evaluation_results_trace_created", "trace_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    trace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("traces.trace_id", ondelete="RESTRICT"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
