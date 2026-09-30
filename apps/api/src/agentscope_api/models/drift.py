from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from agentscope_api.database import Base

METRICS = (
    "'trace_failure_rate', 'trace_success_rate', 'evaluation_pass_rate', "
    "'evaluation_error_rate', 'mean_duration_ms', 'p95_duration_ms', "
    "'mean_total_tokens', 'trace_count'"
)
CLASSIFICATIONS = "'drift_detected', 'no_drift_detected', 'insufficient_evidence'"


class DriftPolicyRecord(Base):
    __tablename__ = "drift_policies"
    __table_args__ = (Index("ix_drift_policies_created_id", "created_at", "id"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2_000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DriftPolicyRuleRecord(Base):
    __tablename__ = "drift_policy_rules"
    __table_args__ = (
        CheckConstraint("position >= 0 AND position < 20", name="ck_drift_rules_position"),
        CheckConstraint(f"metric IN ({METRICS})", name="ck_drift_rules_metric"),
        CheckConstraint("direction IN ('increase', 'decrease')", name="ck_drift_rules_direction"),
        CheckConstraint(
            "threshold_type IN ('absolute', 'relative')", name="ck_drift_rules_threshold_type"
        ),
        CheckConstraint(
            "practical_threshold > 0 AND practical_threshold <= 1000000000",
            name="ck_drift_rules_threshold",
        ),
        CheckConstraint(
            "threshold_type <> 'relative' OR practical_threshold <= 1000",
            name="ck_drift_rules_relative_threshold",
        ),
        CheckConstraint(
            "threshold_type <> 'absolute' OR metric NOT IN "
            "('trace_failure_rate', 'trace_success_rate', 'evaluation_pass_rate', "
            "'evaluation_error_rate') OR practical_threshold <= 1",
            name="ck_drift_rules_rate_threshold",
        ),
        CheckConstraint(
            "minimum_baseline_samples >= 1 AND minimum_baseline_samples <= 1000000 "
            "AND minimum_current_samples >= 1 AND minimum_current_samples <= 1000000",
            name="ck_drift_rules_samples",
        ),
    )

    drift_policy_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("drift_policies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    threshold_type: Mapped[str] = mapped_column(String(20), nullable=False)
    practical_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    minimum_baseline_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_current_samples: Mapped[int] = mapped_column(Integer, nullable=False)


class DriftComparisonRecord(Base):
    __tablename__ = "drift_comparisons"
    __table_args__ = (
        CheckConstraint(
            f"classification IN ({CLASSIFICATIONS})",
            name="ck_drift_comparisons_classification",
        ),
        CheckConstraint(
            "baseline_snapshot_id <> current_snapshot_id",
            name="ck_drift_comparisons_distinct_snapshots",
        ),
        Index("ix_drift_comparisons_created_id", "created_at", "id"),
        Index(
            "ix_drift_comparisons_monitor_created",
            "monitoring_definition_id",
            "created_at",
        ),
        Index("ix_drift_comparisons_policy_created", "drift_policy_id", "created_at"),
        Index("ix_drift_comparisons_current_snapshot", "current_snapshot_id"),
        Index("ix_drift_comparisons_classification_created", "classification", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    monitoring_definition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    drift_policy_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("drift_policies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    baseline_snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    current_snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_name: Mapped[str] = mapped_column(String(200), nullable=False)
    policy_description: Mapped[str | None] = mapped_column(String(2_000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DriftFindingRecord(Base):
    __tablename__ = "drift_findings"
    __table_args__ = (
        CheckConstraint(
            "rule_position >= 0 AND rule_position < 20", name="ck_drift_findings_position"
        ),
        CheckConstraint(f"metric IN ({METRICS})", name="ck_drift_findings_metric"),
        CheckConstraint(
            "direction IN ('increase', 'decrease')", name="ck_drift_findings_direction"
        ),
        CheckConstraint(
            "threshold_type IN ('absolute', 'relative')",
            name="ck_drift_findings_threshold_type",
        ),
        CheckConstraint(
            "practical_threshold > 0 AND practical_threshold <= 1000000000",
            name="ck_drift_findings_threshold",
        ),
        CheckConstraint(
            "threshold_type <> 'relative' OR practical_threshold <= 1000",
            name="ck_drift_findings_relative_threshold",
        ),
        CheckConstraint(
            "threshold_type <> 'absolute' OR metric NOT IN "
            "('trace_failure_rate', 'trace_success_rate', 'evaluation_pass_rate', "
            "'evaluation_error_rate') OR practical_threshold <= 1",
            name="ck_drift_findings_rate_threshold",
        ),
        CheckConstraint(
            "minimum_baseline_samples >= 1 AND minimum_baseline_samples <= 1000000 "
            "AND minimum_current_samples >= 1 AND minimum_current_samples <= 1000000 "
            "AND baseline_sample_count >= 0 AND current_sample_count >= 0",
            name="ck_drift_findings_samples",
        ),
        CheckConstraint(
            f"classification IN ({CLASSIFICATIONS})",
            name="ck_drift_findings_classification",
        ),
        CheckConstraint(
            "(z_statistic IS NULL AND p_value IS NULL) OR "
            "(z_statistic IS NOT NULL AND p_value BETWEEN 0 AND 1)",
            name="ck_drift_findings_statistics",
        ),
    )

    drift_comparison_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("drift_comparisons.id", ondelete="CASCADE"),
        primary_key=True,
    )
    rule_position: Mapped[int] = mapped_column(Integer, primary_key=True)
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    threshold_type: Mapped[str] = mapped_column(String(20), nullable=False)
    practical_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    minimum_baseline_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_current_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    current_sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_value: Mapped[float | None] = mapped_column(Float)
    current_value: Mapped[float | None] = mapped_column(Float)
    absolute_delta: Mapped[float | None] = mapped_column(Float)
    relative_delta: Mapped[float | None] = mapped_column(Float)
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    z_statistic: Mapped[float | None] = mapped_column(Float)
    p_value: Mapped[float | None] = mapped_column(Float)
