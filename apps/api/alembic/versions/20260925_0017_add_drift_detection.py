"""Add baseline-vs-current drift detection."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260925_0017"
down_revision: str | None = "20260925_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

METRICS = (
    "'trace_failure_rate', 'trace_success_rate', 'evaluation_pass_rate', "
    "'evaluation_error_rate', 'mean_duration_ms', 'p95_duration_ms', "
    "'mean_total_tokens', 'trace_count'"
)
CLASSIFICATIONS = "'drift_detected', 'no_drift_detected', 'insufficient_evidence'"


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "drift_policies",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(2_000)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_drift_policies_created_id", "drift_policies", ["created_at", "id"])

    op.create_table(
        "drift_policy_rules",
        sa.Column(
            "drift_policy_id",
            uuid,
            sa.ForeignKey("drift_policies.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("threshold_type", sa.String(20), nullable=False),
        sa.Column("practical_threshold", sa.Float(), nullable=False),
        sa.Column("minimum_baseline_samples", sa.Integer(), nullable=False),
        sa.Column("minimum_current_samples", sa.Integer(), nullable=False),
        sa.CheckConstraint("position >= 0 AND position < 20", name="ck_drift_rules_position"),
        sa.CheckConstraint(f"metric IN ({METRICS})", name="ck_drift_rules_metric"),
        sa.CheckConstraint(
            "direction IN ('increase', 'decrease')", name="ck_drift_rules_direction"
        ),
        sa.CheckConstraint(
            "threshold_type IN ('absolute', 'relative')", name="ck_drift_rules_threshold_type"
        ),
        sa.CheckConstraint(
            "practical_threshold > 0 AND practical_threshold <= 1000000000",
            name="ck_drift_rules_threshold",
        ),
        sa.CheckConstraint(
            "threshold_type <> 'relative' OR practical_threshold <= 1000",
            name="ck_drift_rules_relative_threshold",
        ),
        sa.CheckConstraint(
            "threshold_type <> 'absolute' OR metric NOT IN "
            "('trace_failure_rate', 'trace_success_rate', 'evaluation_pass_rate', "
            "'evaluation_error_rate') OR practical_threshold <= 1",
            name="ck_drift_rules_rate_threshold",
        ),
        sa.CheckConstraint(
            "minimum_baseline_samples >= 1 AND minimum_baseline_samples <= 1000000 "
            "AND minimum_current_samples >= 1 AND minimum_current_samples <= 1000000",
            name="ck_drift_rules_samples",
        ),
    )

    op.create_table(
        "drift_comparisons",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "monitoring_definition_id",
            uuid,
            sa.ForeignKey("monitoring_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "drift_policy_id",
            uuid,
            sa.ForeignKey("drift_policies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "baseline_snapshot_id",
            uuid,
            sa.ForeignKey("monitoring_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "current_snapshot_id",
            uuid,
            sa.ForeignKey("monitoring_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("policy_name", sa.String(200), nullable=False),
        sa.Column("policy_description", sa.String(2_000)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            f"classification IN ({CLASSIFICATIONS})",
            name="ck_drift_comparisons_classification",
        ),
        sa.CheckConstraint(
            "baseline_snapshot_id <> current_snapshot_id",
            name="ck_drift_comparisons_distinct_snapshots",
        ),
    )
    op.create_index("ix_drift_comparisons_created_id", "drift_comparisons", ["created_at", "id"])
    op.create_index(
        "ix_drift_comparisons_monitor_created",
        "drift_comparisons",
        ["monitoring_definition_id", "created_at"],
    )
    op.create_index(
        "ix_drift_comparisons_policy_created",
        "drift_comparisons",
        ["drift_policy_id", "created_at"],
    )
    op.create_index(
        "ix_drift_comparisons_current_snapshot",
        "drift_comparisons",
        ["current_snapshot_id"],
    )
    op.create_index(
        "ix_drift_comparisons_classification_created",
        "drift_comparisons",
        ["classification", "created_at"],
    )

    op.create_table(
        "drift_findings",
        sa.Column(
            "drift_comparison_id",
            uuid,
            sa.ForeignKey("drift_comparisons.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rule_position", sa.Integer(), primary_key=True),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("threshold_type", sa.String(20), nullable=False),
        sa.Column("practical_threshold", sa.Float(), nullable=False),
        sa.Column("minimum_baseline_samples", sa.Integer(), nullable=False),
        sa.Column("minimum_current_samples", sa.Integer(), nullable=False),
        sa.Column("baseline_sample_count", sa.Integer(), nullable=False),
        sa.Column("current_sample_count", sa.Integer(), nullable=False),
        sa.Column("baseline_value", sa.Float()),
        sa.Column("current_value", sa.Float()),
        sa.Column("absolute_delta", sa.Float()),
        sa.Column("relative_delta", sa.Float()),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("z_statistic", sa.Float()),
        sa.Column("p_value", sa.Float()),
        sa.CheckConstraint(
            "rule_position >= 0 AND rule_position < 20", name="ck_drift_findings_position"
        ),
        sa.CheckConstraint(f"metric IN ({METRICS})", name="ck_drift_findings_metric"),
        sa.CheckConstraint(
            "direction IN ('increase', 'decrease')", name="ck_drift_findings_direction"
        ),
        sa.CheckConstraint(
            "threshold_type IN ('absolute', 'relative')",
            name="ck_drift_findings_threshold_type",
        ),
        sa.CheckConstraint(
            "practical_threshold > 0 AND practical_threshold <= 1000000000",
            name="ck_drift_findings_threshold",
        ),
        sa.CheckConstraint(
            "threshold_type <> 'relative' OR practical_threshold <= 1000",
            name="ck_drift_findings_relative_threshold",
        ),
        sa.CheckConstraint(
            "threshold_type <> 'absolute' OR metric NOT IN "
            "('trace_failure_rate', 'trace_success_rate', 'evaluation_pass_rate', "
            "'evaluation_error_rate') OR practical_threshold <= 1",
            name="ck_drift_findings_rate_threshold",
        ),
        sa.CheckConstraint(
            "minimum_baseline_samples >= 1 AND minimum_baseline_samples <= 1000000 "
            "AND minimum_current_samples >= 1 AND minimum_current_samples <= 1000000 "
            "AND baseline_sample_count >= 0 AND current_sample_count >= 0",
            name="ck_drift_findings_samples",
        ),
        sa.CheckConstraint(
            f"classification IN ({CLASSIFICATIONS})",
            name="ck_drift_findings_classification",
        ),
        sa.CheckConstraint(
            "(z_statistic IS NULL AND p_value IS NULL) OR "
            "(z_statistic IS NOT NULL AND p_value BETWEEN 0 AND 1)",
            name="ck_drift_findings_statistics",
        ),
    )


def downgrade() -> None:
    op.drop_table("drift_findings")
    # Tolerates an unreleased local draft applied before this index was added.
    op.execute("DROP INDEX IF EXISTS ix_drift_comparisons_classification_created")
    op.drop_index("ix_drift_comparisons_current_snapshot", table_name="drift_comparisons")
    op.drop_index("ix_drift_comparisons_policy_created", table_name="drift_comparisons")
    op.drop_index("ix_drift_comparisons_monitor_created", table_name="drift_comparisons")
    op.drop_index("ix_drift_comparisons_created_id", table_name="drift_comparisons")
    op.drop_table("drift_comparisons")
    op.drop_table("drift_policy_rules")
    op.drop_index("ix_drift_policies_created_id", table_name="drift_policies")
    op.drop_table("drift_policies")
