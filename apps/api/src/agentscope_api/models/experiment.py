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


class ExperimentRecord(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'ready', 'running', 'completed', 'failed')",
            name="ck_experiments_status",
        ),
        CheckConstraint(
            "(status = 'draft' AND ready_at IS NULL) OR "
            "(status <> 'draft' AND ready_at IS NOT NULL)",
            name="ck_experiments_ready_at",
        ),
        Index("ix_experiments_created_id", "created_at", "id"),
        Index("ix_experiments_status_created", "status", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2_000))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentVariantRecord(Base):
    __tablename__ = "experiment_variants"
    __table_args__ = (
        UniqueConstraint("experiment_id", "variant_key", name="uq_experiment_variants_key"),
        CheckConstraint("variant_key IN ('A', 'B')", name="ck_experiment_variants_key"),
        CheckConstraint(
            "jsonb_typeof(provenance) = 'object'", name="ck_experiment_variants_provenance"
        ),
        Index("ix_experiment_variants_experiment", "experiment_id", "variant_key"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    experiment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    variant_key: Mapped[str] = mapped_column(String(1), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExperimentSubjectRecord(Base):
    __tablename__ = "experiment_subjects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_id", "variant_key"],
            ["experiment_variants.experiment_id", "experiment_variants.variant_key"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "experiment_id", "trace_id", name="uq_experiment_subjects_trace"
        ),
        UniqueConstraint(
            "experiment_id",
            "position",
            "variant_key",
            "trace_id",
            name="uq_experiment_subjects_identity",
        ),
        CheckConstraint("variant_key IN ('A', 'B')", name="ck_experiment_subjects_key"),
        CheckConstraint(
            "position >= 0 AND position < 1000", name="ck_experiment_subjects_position"
        ),
        Index("ix_experiment_subjects_trace", "trace_id"),
        Index("ix_experiment_subjects_order", "experiment_id", "position", "variant_key"),
    )

    experiment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_key: Mapped[str] = mapped_column(String(1), primary_key=True)
    trace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("traces.trace_id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExperimentEvaluationConditionRecord(Base):
    __tablename__ = "experiment_evaluation_conditions"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id", "definition_id", name="uq_experiment_conditions_definition"
        ),
        UniqueConstraint(
            "experiment_id",
            "position",
            "definition_id",
            name="uq_experiment_conditions_identity",
        ),
        CheckConstraint(
            "position >= 0 AND position < 20", name="ck_experiment_conditions_position"
        ),
        CheckConstraint(
            "jsonb_typeof(evaluator_config) = 'object'",
            name="ck_experiment_conditions_config",
        ),
        Index("ix_experiment_conditions_definition", "definition_id"),
    )

    experiment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("experiments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    definition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("evaluation_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    definition_name: Mapped[str] = mapped_column(String(200), nullable=False)
    evaluator_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    evaluator_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExperimentRunRecord(Base):
    __tablename__ = "experiment_runs"
    __table_args__ = (
        UniqueConstraint("id", "experiment_id", name="uq_experiment_runs_id_experiment"),
        CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'completed', 'failed')",
            name="ck_experiment_runs_status",
        ),
        CheckConstraint(
            "expected_decision_count >= 1 AND expected_decision_count <= 40000",
            name="ck_experiment_runs_expected_count",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 4",
            name="ck_experiment_runs_attempt_count",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_experiment_runs_lease",
        ),
        CheckConstraint(
            "(status IN ('pending', 'queued', 'running') AND completed_at IS NULL "
            "AND error_category IS NULL AND error_message IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND error_category IS NULL AND error_message IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL "
            "AND error_category IS NOT NULL)",
            name="ck_experiment_runs_lifecycle",
        ),
        Index("ix_experiment_runs_created_id", "created_at", "id"),
        Index("ix_experiment_runs_experiment_created", "experiment_id", "created_at", "id"),
        Index("ix_experiment_runs_recovery", "status", "lease_expires_at", "last_enqueued_at"),
        Index(
            "uq_experiment_runs_one_active",
            "experiment_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'queued', 'running')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    experiment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("experiments.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    expected_decision_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(String(4_000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentRunResultRecord(Base):
    __tablename__ = "experiment_run_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "experiment_id"],
            ["experiment_runs.id", "experiment_runs.experiment_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["experiment_id", "subject_position", "variant_key", "trace_id"],
            [
                "experiment_subjects.experiment_id",
                "experiment_subjects.position",
                "experiment_subjects.variant_key",
                "experiment_subjects.trace_id",
            ],
            name="fk_experiment_results_subject_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["experiment_id", "condition_position", "definition_id"],
            [
                "experiment_evaluation_conditions.experiment_id",
                "experiment_evaluation_conditions.position",
                "experiment_evaluation_conditions.definition_id",
            ],
            name="fk_experiment_results_condition_identity",
            ondelete="RESTRICT",
        ),
        CheckConstraint("variant_key IN ('A', 'B')", name="ck_experiment_results_variant"),
        CheckConstraint(
            "outcome IN ('passed', 'failed', 'error')", name="ck_experiment_results_outcome"
        ),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="ck_experiment_results_score",
        ),
        CheckConstraint(
            "outcome <> 'error' OR score IS NULL", name="ck_experiment_results_error_score"
        ),
        CheckConstraint(
            "jsonb_typeof(details) = 'object'", name="ck_experiment_results_details"
        ),
        CheckConstraint(
            "attempt_count >= 1 AND attempt_count <= 4",
            name="ck_experiment_results_attempt_count",
        ),
        Index(
            "ix_experiment_results_order",
            "run_id",
            "subject_position",
            "variant_key",
            "condition_position",
        ),
        Index("ix_experiment_results_trace", "trace_id", "created_at"),
        Index("ix_experiment_results_condition", "run_id", "condition_position"),
    )

    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    experiment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    subject_position: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_key: Mapped[str] = mapped_column(String(1), primary_key=True)
    condition_position: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    definition_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExperimentRunAnalysisRecord(Base):
    __tablename__ = "experiment_run_analyses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "experiment_id"],
            ["experiment_runs.id", "experiment_runs.experiment_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("run_id", name="uq_experiment_run_analyses_run"),
        UniqueConstraint(
            "id", "experiment_id", name="uq_experiment_run_analyses_identity"
        ),
        CheckConstraint(
            "analysis_schema_version = '1'",
            name="ck_experiment_run_analyses_schema",
        ),
        CheckConstraint(
            "confidence_method = 'paired_percentile_bootstrap' "
            "AND confidence_level > 0 AND confidence_level < 1 "
            "AND bootstrap_iterations >= 1 AND bootstrap_iterations <= 1000000",
            name="ck_experiment_run_analyses_confidence",
        ),
        CheckConstraint(
            "hypothesis_test_method = 'exact_two_sided_mcnemar_binomial' "
            "AND alpha > 0 AND alpha < 1",
            name="ck_experiment_run_analyses_test",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    experiment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    analysis_schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence_method: Mapped[str] = mapped_column(String(60), nullable=False)
    confidence_level: Mapped[float] = mapped_column(Float, nullable=False)
    bootstrap_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    bootstrap_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    hypothesis_test_method: Mapped[str] = mapped_column(String(60), nullable=False)
    alpha: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExperimentConditionAnalysisRecord(Base):
    __tablename__ = "experiment_condition_analyses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analysis_id", "experiment_id"],
            ["experiment_run_analyses.id", "experiment_run_analyses.experiment_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["experiment_id", "condition_position", "definition_id"],
            [
                "experiment_evaluation_conditions.experiment_id",
                "experiment_evaluation_conditions.position",
                "experiment_evaluation_conditions.definition_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(eligible AND ineligible_reason IS NULL AND "
            "num_nonnulls(sample_size, a_passed_count, a_failed_count, b_passed_count, "
            "b_failed_count, a_pass_rate, b_pass_rate, pass_rate_difference, "
            "both_passed_count, both_passed_rate, both_failed_count, both_failed_rate, "
            "a_only_passed_count, a_only_passed_rate, b_only_passed_count, "
            "b_only_passed_rate, confidence_interval_lower, confidence_interval_upper, "
            "discordant_count, p_value, rejects_null) = 21) OR "
            "(NOT eligible AND ineligible_reason = 'non_binary_outcome' AND "
            "num_nonnulls(sample_size, a_passed_count, a_failed_count, b_passed_count, "
            "b_failed_count, a_pass_rate, b_pass_rate, pass_rate_difference, "
            "both_passed_count, both_passed_rate, both_failed_count, both_failed_rate, "
            "a_only_passed_count, a_only_passed_rate, b_only_passed_count, "
            "b_only_passed_rate, matched_pairs_odds_ratio, confidence_interval_lower, "
            "confidence_interval_upper, discordant_count, p_value, rejects_null) = 0)",
            name="ck_experiment_condition_analyses_eligibility",
        ),
        CheckConstraint(
            "sample_size IS NULL OR (sample_size >= 1 AND sample_size <= 1000 AND "
            "a_passed_count + a_failed_count = sample_size AND "
            "b_passed_count + b_failed_count = sample_size AND "
            "both_passed_count + both_failed_count + a_only_passed_count + "
            "b_only_passed_count = sample_size AND "
            "a_passed_count = both_passed_count + a_only_passed_count AND "
            "b_passed_count = both_passed_count + b_only_passed_count AND "
            "discordant_count = a_only_passed_count + b_only_passed_count)",
            name="ck_experiment_condition_analyses_counts",
        ),
        CheckConstraint(
            "a_pass_rate IS NULL OR (a_pass_rate BETWEEN 0 AND 1 AND "
            "b_pass_rate BETWEEN 0 AND 1 AND pass_rate_difference BETWEEN -1 AND 1 AND "
            "both_passed_rate BETWEEN 0 AND 1 AND both_failed_rate BETWEEN 0 AND 1 AND "
            "a_only_passed_rate BETWEEN 0 AND 1 AND b_only_passed_rate BETWEEN 0 AND 1 AND "
            "(matched_pairs_odds_ratio IS NULL OR "
            "matched_pairs_odds_ratio BETWEEN 0 AND 1000) AND "
            "confidence_interval_lower BETWEEN -1 AND 1 AND "
            "confidence_interval_upper BETWEEN -1 AND 1 AND "
            "confidence_interval_lower <= confidence_interval_upper AND "
            "p_value BETWEEN 0 AND 1)",
            name="ck_experiment_condition_analyses_metrics",
        ),
    )

    analysis_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    experiment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    condition_position: Mapped[int] = mapped_column(Integer, primary_key=True)
    definition_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ineligible_reason: Mapped[str | None] = mapped_column(String(60))
    sample_size: Mapped[int | None] = mapped_column(Integer)
    a_passed_count: Mapped[int | None] = mapped_column(Integer)
    a_failed_count: Mapped[int | None] = mapped_column(Integer)
    b_passed_count: Mapped[int | None] = mapped_column(Integer)
    b_failed_count: Mapped[int | None] = mapped_column(Integer)
    a_pass_rate: Mapped[float | None] = mapped_column(Float)
    b_pass_rate: Mapped[float | None] = mapped_column(Float)
    pass_rate_difference: Mapped[float | None] = mapped_column(Float)
    both_passed_count: Mapped[int | None] = mapped_column(Integer)
    both_passed_rate: Mapped[float | None] = mapped_column(Float)
    both_failed_count: Mapped[int | None] = mapped_column(Integer)
    both_failed_rate: Mapped[float | None] = mapped_column(Float)
    a_only_passed_count: Mapped[int | None] = mapped_column(Integer)
    a_only_passed_rate: Mapped[float | None] = mapped_column(Float)
    b_only_passed_count: Mapped[int | None] = mapped_column(Integer)
    b_only_passed_rate: Mapped[float | None] = mapped_column(Float)
    matched_pairs_odds_ratio: Mapped[float | None] = mapped_column(Float)
    confidence_interval_lower: Mapped[float | None] = mapped_column(Float)
    confidence_interval_upper: Mapped[float | None] = mapped_column(Float)
    discordant_count: Mapped[int | None] = mapped_column(Integer)
    p_value: Mapped[float | None] = mapped_column(Float)
    rejects_null: Mapped[bool | None] = mapped_column(Boolean)
