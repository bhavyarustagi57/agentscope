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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from agentscope_api.database import Base


class RegressionPolicyRecord(Base):
    __tablename__ = "regression_policies"
    __table_args__ = (
        CheckConstraint(
            "minimum_pass_rate_drop > 0 AND minimum_pass_rate_drop <= 1",
            name="ck_regression_policies_drop",
        ),
        CheckConstraint(
            "minimum_sample_size >= 1 AND minimum_sample_size <= 1000",
            name="ck_regression_policies_sample",
        ),
        Index("ix_regression_policies_created_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2_000))
    minimum_pass_rate_drop: Mapped[float] = mapped_column(Float, nullable=False)
    minimum_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RegressionCheckRecord(Base):
    __tablename__ = "regression_checks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_run_id", "experiment_id"],
            ["experiment_runs.id", "experiment_runs.experiment_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["analysis_id", "experiment_id"],
            ["experiment_run_analyses.id", "experiment_run_analyses.experiment_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "experiment_run_id",
            "regression_policy_id",
            "baseline_variant",
            "candidate_variant",
            name="uq_regression_checks_request",
        ),
        CheckConstraint(
            "baseline_variant IN ('A', 'B') AND candidate_variant IN ('A', 'B') "
            "AND baseline_variant <> candidate_variant",
            name="ck_regression_checks_orientation",
        ),
        CheckConstraint(
            "classification IN ('regression_detected', 'no_regression_detected', "
            "'insufficient_evidence')",
            name="ck_regression_checks_classification",
        ),
        CheckConstraint(
            "minimum_pass_rate_drop > 0 AND minimum_pass_rate_drop <= 1 "
            "AND minimum_sample_size >= 1 AND minimum_sample_size <= 1000",
            name="ck_regression_checks_policy_snapshot",
        ),
        CheckConstraint(
            "jsonb_typeof(baseline_provenance) = 'object' "
            "AND jsonb_typeof(candidate_provenance) = 'object'",
            name="ck_regression_checks_provenance",
        ),
        Index("ix_regression_checks_created_id", "created_at", "id"),
        Index("ix_regression_checks_run", "experiment_run_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    experiment_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    experiment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    analysis_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    regression_policy_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("regression_policies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    baseline_variant: Mapped[str] = mapped_column(String(1), nullable=False)
    candidate_variant: Mapped[str] = mapped_column(String(1), nullable=False)
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_name: Mapped[str] = mapped_column(String(200), nullable=False)
    policy_description: Mapped[str | None] = mapped_column(String(2_000))
    minimum_pass_rate_drop: Mapped[float] = mapped_column(Float, nullable=False)
    minimum_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    candidate_provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RegressionFindingRecord(Base):
    __tablename__ = "regression_findings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analysis_id", "condition_position"],
            [
                "experiment_condition_analyses.analysis_id",
                "experiment_condition_analyses.condition_position",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "classification IN ('regression_detected', 'no_regression_detected', "
            "'insufficient_evidence')",
            name="ck_regression_findings_classification",
        ),
        CheckConstraint(
            "baseline_variant IN ('A', 'B') AND candidate_variant IN ('A', 'B') "
            "AND baseline_variant <> candidate_variant",
            name="ck_regression_findings_orientation",
        ),
        CheckConstraint(
            "jsonb_typeof(evaluator_config) = 'object'",
            name="ck_regression_findings_config",
        ),
        CheckConstraint(
            "condition_position >= 0 AND condition_position < 20",
            name="ck_regression_findings_position",
        ),
        CheckConstraint(
            "minimum_pass_rate_drop > 0 AND minimum_pass_rate_drop <= 1 "
            "AND minimum_sample_size >= 1 AND minimum_sample_size <= 1000",
            name="ck_regression_findings_policy_snapshot",
        ),
        CheckConstraint(
            "(source_eligible AND source_ineligible_reason IS NULL AND "
            "num_nonnulls(sample_size, baseline_passed_count, candidate_passed_count, "
            "baseline_pass_rate, candidate_pass_rate, candidate_minus_baseline, "
            "both_passed_count, both_failed_count, baseline_only_passed_count, "
            "candidate_only_passed_count, discordant_count, p_value, rejects_null, "
            "source_interval_lower, source_interval_upper, confidence_interval_lower, "
            "confidence_interval_upper) = 17) OR "
            "(NOT source_eligible AND source_ineligible_reason IS NOT NULL AND "
            "classification = 'insufficient_evidence' AND "
            "num_nonnulls(sample_size, baseline_passed_count, candidate_passed_count, "
            "baseline_pass_rate, candidate_pass_rate, candidate_minus_baseline, "
            "both_passed_count, both_failed_count, baseline_only_passed_count, "
            "candidate_only_passed_count, discordant_count, matched_pairs_odds_ratio, "
            "p_value, rejects_null, source_interval_lower, source_interval_upper, "
            "confidence_interval_lower, confidence_interval_upper) = 0)",
            name="ck_regression_findings_eligibility",
        ),
        CheckConstraint(
            "sample_size IS NULL OR (sample_size >= 1 AND "
            "baseline_passed_count = both_passed_count + baseline_only_passed_count AND "
            "candidate_passed_count = both_passed_count + candidate_only_passed_count AND "
            "both_passed_count + both_failed_count + baseline_only_passed_count + "
            "candidate_only_passed_count = sample_size AND "
            "discordant_count = baseline_only_passed_count + candidate_only_passed_count)",
            name="ck_regression_findings_counts",
        ),
        CheckConstraint(
            "baseline_pass_rate IS NULL OR (baseline_pass_rate BETWEEN 0 AND 1 AND "
            "candidate_pass_rate BETWEEN 0 AND 1 AND candidate_minus_baseline BETWEEN -1 AND 1 "
            "AND p_value BETWEEN 0 AND 1 AND source_interval_lower BETWEEN -1 AND 1 "
            "AND source_interval_upper BETWEEN -1 AND 1 "
            "AND confidence_interval_lower BETWEEN -1 AND 1 "
            "AND confidence_interval_upper BETWEEN -1 AND 1 "
            "AND source_interval_lower <= source_interval_upper "
            "AND confidence_interval_lower <= confidence_interval_upper "
            "AND (matched_pairs_odds_ratio IS NULL OR "
            "matched_pairs_odds_ratio BETWEEN 0 AND 1000))",
            name="ck_regression_findings_metrics",
        ),
        Index("ix_regression_findings_definition", "definition_id", "created_at"),
    )

    regression_check_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("regression_checks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    condition_position: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    definition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("evaluation_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    definition_name: Mapped[str] = mapped_column(String(200), nullable=False)
    evaluator_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    evaluator_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    baseline_variant: Mapped[str] = mapped_column(String(1), nullable=False)
    candidate_variant: Mapped[str] = mapped_column(String(1), nullable=False)
    source_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_ineligible_reason: Mapped[str | None] = mapped_column(String(60))
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    minimum_pass_rate_drop: Mapped[float] = mapped_column(Float, nullable=False)
    minimum_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_size: Mapped[int | None] = mapped_column(Integer)
    baseline_passed_count: Mapped[int | None] = mapped_column(Integer)
    candidate_passed_count: Mapped[int | None] = mapped_column(Integer)
    baseline_pass_rate: Mapped[float | None] = mapped_column(Float)
    candidate_pass_rate: Mapped[float | None] = mapped_column(Float)
    candidate_minus_baseline: Mapped[float | None] = mapped_column(Float)
    both_passed_count: Mapped[int | None] = mapped_column(Integer)
    both_failed_count: Mapped[int | None] = mapped_column(Integer)
    baseline_only_passed_count: Mapped[int | None] = mapped_column(Integer)
    candidate_only_passed_count: Mapped[int | None] = mapped_column(Integer)
    discordant_count: Mapped[int | None] = mapped_column(Integer)
    matched_pairs_odds_ratio: Mapped[float | None] = mapped_column(Float)
    p_value: Mapped[float | None] = mapped_column(Float)
    rejects_null: Mapped[bool | None] = mapped_column(Boolean)
    source_interval_lower: Mapped[float | None] = mapped_column(Float)
    source_interval_upper: Mapped[float | None] = mapped_column(Float)
    confidence_interval_lower: Mapped[float | None] = mapped_column(Float)
    confidence_interval_upper: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
