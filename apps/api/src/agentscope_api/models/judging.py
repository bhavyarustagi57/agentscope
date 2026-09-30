from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from agentscope_api.database import Base


class JudgeConfigurationRecord(Base):
    __tablename__ = "judge_configurations"
    __table_args__ = (
        CheckConstraint("provider = 'openai'", name="ck_judge_configurations_provider"),
        CheckConstraint("output_schema_version = '1'", name="ck_judge_configurations_schema"),
        CheckConstraint("configuration_version = '1'", name="ck_judge_configurations_version"),
        CheckConstraint(
            "timeout_seconds >= 5 AND timeout_seconds <= 300",
            name="ck_judge_configurations_timeout",
        ),
        CheckConstraint(
            "max_output_tokens >= 32 AND max_output_tokens <= 1000",
            name="ck_judge_configurations_tokens",
        ),
        Index("ix_judge_configurations_created_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(2_000))
    provider: Mapped[str] = mapped_column(String(20), default="openai")
    model: Mapped[str] = mapped_column(String(200))
    rubric: Mapped[str] = mapped_column(String(8_000))
    output_schema_version: Mapped[str] = mapped_column(String(20), default="1")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=300)
    configuration_version: Mapped[str] = mapped_column(String(20), default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JudgeRunRecord(Base):
    __tablename__ = "calibration_judge_runs"
    __table_args__ = (
        UniqueConstraint("id", "study_id", name="uq_judge_runs_id_study"),
        CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'completed', 'failed')",
            name="ck_judge_runs_status",
        ),
        CheckConstraint("provider = 'openai'", name="ck_judge_runs_provider"),
        CheckConstraint("output_schema_version = '1'", name="ck_judge_runs_schema"),
        CheckConstraint("configuration_version = '1'", name="ck_judge_runs_version"),
        CheckConstraint("attempt_count >= 0 AND attempt_count <= 3", name="ck_judge_runs_attempts"),
        CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_judge_runs_lease",
        ),
        CheckConstraint(
            "(status IN ('pending', 'queued') AND completed_at IS NULL "
            "AND error_category IS NULL) OR "
            "(status = 'running' AND completed_at IS NULL AND error_category IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND error_category IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND error_category IS NOT NULL)",
            name="ck_judge_runs_lifecycle",
        ),
        Index("ix_judge_runs_created_id", "created_at", "id"),
        Index("ix_judge_runs_study_created", "study_id", "created_at", "id"),
        Index("ix_judge_runs_recovery", "status", "lease_expires_at", "last_enqueued_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    study_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("calibration_studies.id", ondelete="RESTRICT")
    )
    configuration_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("judge_configurations.id", ondelete="RESTRICT")
    )
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(200))
    rubric: Mapped[str] = mapped_column(String(8_000))
    output_schema_version: Mapped[str] = mapped_column(String(20))
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    configuration_version: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    error_category: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JudgeResultRecord(Base):
    __tablename__ = "calibration_judge_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "study_id"],
            ["calibration_judge_runs.id", "calibration_judge_runs.study_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["study_id", "trace_id"],
            ["calibration_study_subjects.study_id", "calibration_study_subjects.trace_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(decision IN ('passed', 'failed') AND rationale IS NOT NULL "
            "AND error_category IS NULL) "
            "OR (decision IS NULL AND rationale IS NULL AND error_category IS NOT NULL)",
            name="ck_judge_results_outcome",
        ),
        CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) AND "
            "(output_tokens IS NULL OR output_tokens >= 0) AND "
            "(total_tokens IS NULL OR total_tokens >= 0)",
            name="ck_judge_results_tokens",
        ),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_judge_results_latency"),
        CheckConstraint(
            "attempt_count >= 1 AND attempt_count <= 3", name="ck_judge_results_attempt"
        ),
        Index("ix_judge_results_run_created", "run_id", "created_at", "trace_id"),
        Index("ix_judge_results_trace", "trace_id", "created_at"),
    )

    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    study_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    decision: Mapped[str | None] = mapped_column(String(20))
    rationale: Mapped[str | None] = mapped_column(String(4_000))
    error_category: Mapped[str | None] = mapped_column(String(40))
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(200))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    attempt_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CalibrationAnalysisRecord(Base):
    __tablename__ = "calibration_analyses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "study_id"],
            ["calibration_judge_runs.id", "calibration_judge_runs.study_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "sample_count >= 1 AND sample_count <= 500 AND "
            "human_passed_count >= 0 AND human_failed_count >= 0 AND "
            "judge_passed_count >= 0 AND judge_failed_count >= 0 AND "
            "agreement_count >= 0 AND disagreement_count >= 0 AND "
            "true_positive >= 0 AND true_negative >= 0 AND "
            "false_positive >= 0 AND false_negative >= 0",
            name="ck_calibration_analyses_nonnegative_counts",
        ),
        CheckConstraint(
            "true_positive + true_negative + false_positive + false_negative = sample_count "
            "AND agreement_count = true_positive + true_negative "
            "AND disagreement_count = false_positive + false_negative "
            "AND agreement_count + disagreement_count = sample_count",
            name="ck_calibration_analyses_matrix_counts",
        ),
        CheckConstraint(
            "human_passed_count = true_positive + false_negative "
            "AND human_failed_count = true_negative + false_positive "
            "AND judge_passed_count = true_positive + false_positive "
            "AND judge_failed_count = true_negative + false_negative",
            name="ck_calibration_analyses_marginal_counts",
        ),
        CheckConstraint(
            "observed_agreement >= 0 AND observed_agreement <= 1 "
            "AND expected_agreement >= 0 AND expected_agreement <= 1 "
            "AND (precision_passed IS NULL OR "
            "(precision_passed >= 0 AND precision_passed <= 1)) "
            "AND (recall_passed IS NULL OR (recall_passed >= 0 AND recall_passed <= 1)) "
            "AND (f1_passed IS NULL OR (f1_passed >= 0 AND f1_passed <= 1)) "
            "AND (specificity_failed IS NULL OR "
            "(specificity_failed >= 0 AND specificity_failed <= 1)) "
            "AND (cohens_kappa IS NULL OR (cohens_kappa >= -1 AND cohens_kappa <= 1))",
            name="ck_calibration_analyses_metric_bounds",
        ),
        CheckConstraint(
            "metric_schema_version = '1'", name="ck_calibration_analyses_schema_version"
        ),
        Index("ix_calibration_analyses_study_created", "study_id", "created_at", "run_id"),
    )

    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    study_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer)
    human_passed_count: Mapped[int] = mapped_column(Integer)
    human_failed_count: Mapped[int] = mapped_column(Integer)
    judge_passed_count: Mapped[int] = mapped_column(Integer)
    judge_failed_count: Mapped[int] = mapped_column(Integer)
    agreement_count: Mapped[int] = mapped_column(Integer)
    disagreement_count: Mapped[int] = mapped_column(Integer)
    true_positive: Mapped[int] = mapped_column(Integer)
    true_negative: Mapped[int] = mapped_column(Integer)
    false_positive: Mapped[int] = mapped_column(Integer)
    false_negative: Mapped[int] = mapped_column(Integer)
    observed_agreement: Mapped[float] = mapped_column(Float)
    expected_agreement: Mapped[float] = mapped_column(Float)
    precision_passed: Mapped[float | None] = mapped_column(Float)
    recall_passed: Mapped[float | None] = mapped_column(Float)
    f1_passed: Mapped[float | None] = mapped_column(Float)
    specificity_failed: Mapped[float | None] = mapped_column(Float)
    cohens_kappa: Mapped[float | None] = mapped_column(Float)
    metric_schema_version: Mapped[str] = mapped_column(String(20), default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
