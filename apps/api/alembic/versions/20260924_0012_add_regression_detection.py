"""Add regression detection policies and evidence."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260924_0012"
down_revision: str | None = "20260923_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "regression_policies",
        sa.Column("id", uuid, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(2_000)),
        sa.Column("minimum_pass_rate_drop", sa.Float(), nullable=False),
        sa.Column("minimum_sample_size", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "minimum_pass_rate_drop > 0 AND minimum_pass_rate_drop <= 1",
            name="ck_regression_policies_drop",
        ),
        sa.CheckConstraint(
            "minimum_sample_size >= 1 AND minimum_sample_size <= 1000",
            name="ck_regression_policies_sample",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_regression_policies_created_id",
        "regression_policies",
        ["created_at", "id"],
    )
    classification = (
        "classification IN ('regression_detected', 'no_regression_detected', "
        "'insufficient_evidence')"
    )
    op.create_table(
        "regression_checks",
        sa.Column("id", uuid, nullable=False),
        sa.Column("experiment_run_id", uuid, nullable=False),
        sa.Column("experiment_id", uuid, nullable=False),
        sa.Column("analysis_id", uuid, nullable=False),
        sa.Column("regression_policy_id", uuid, nullable=False),
        sa.Column("baseline_variant", sa.String(1), nullable=False),
        sa.Column("candidate_variant", sa.String(1), nullable=False),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("policy_name", sa.String(200), nullable=False),
        sa.Column("policy_description", sa.String(2_000)),
        sa.Column("minimum_pass_rate_drop", sa.Float(), nullable=False),
        sa.Column("minimum_sample_size", sa.Integer(), nullable=False),
        sa.Column("baseline_provenance", postgresql.JSONB(), nullable=False),
        sa.Column("candidate_provenance", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["experiment_run_id", "experiment_id"],
            ["experiment_runs.id", "experiment_runs.experiment_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id", "experiment_id"],
            ["experiment_run_analyses.id", "experiment_run_analyses.experiment_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["regression_policy_id"], ["regression_policies.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_run_id",
            "regression_policy_id",
            "baseline_variant",
            "candidate_variant",
            name="uq_regression_checks_request",
        ),
        sa.CheckConstraint(
            "baseline_variant IN ('A', 'B') AND candidate_variant IN ('A', 'B') "
            "AND baseline_variant <> candidate_variant",
            name="ck_regression_checks_orientation",
        ),
        sa.CheckConstraint(classification, name="ck_regression_checks_classification"),
        sa.CheckConstraint(
            "minimum_pass_rate_drop > 0 AND minimum_pass_rate_drop <= 1 "
            "AND minimum_sample_size >= 1 AND minimum_sample_size <= 1000",
            name="ck_regression_checks_policy_snapshot",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(baseline_provenance) = 'object' "
            "AND jsonb_typeof(candidate_provenance) = 'object'",
            name="ck_regression_checks_provenance",
        ),
    )
    op.create_index("ix_regression_checks_created_id", "regression_checks", ["created_at", "id"])
    op.create_index(
        "ix_regression_checks_run",
        "regression_checks",
        ["experiment_run_id", "created_at"],
    )
    op.create_table(
        "regression_findings",
        sa.Column("regression_check_id", uuid, nullable=False),
        sa.Column("condition_position", sa.Integer(), nullable=False),
        sa.Column("analysis_id", uuid, nullable=False),
        sa.Column("definition_id", uuid, nullable=False),
        sa.Column("definition_name", sa.String(200), nullable=False),
        sa.Column("evaluator_kind", sa.String(40), nullable=False),
        sa.Column("evaluator_config", postgresql.JSONB(), nullable=False),
        sa.Column("baseline_variant", sa.String(1), nullable=False),
        sa.Column("candidate_variant", sa.String(1), nullable=False),
        sa.Column("source_eligible", sa.Boolean(), nullable=False),
        sa.Column("source_ineligible_reason", sa.String(60)),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("minimum_pass_rate_drop", sa.Float(), nullable=False),
        sa.Column("minimum_sample_size", sa.Integer(), nullable=False),
        sa.Column("sample_size", sa.Integer()),
        sa.Column("baseline_passed_count", sa.Integer()),
        sa.Column("candidate_passed_count", sa.Integer()),
        sa.Column("baseline_pass_rate", sa.Float()),
        sa.Column("candidate_pass_rate", sa.Float()),
        sa.Column("candidate_minus_baseline", sa.Float()),
        sa.Column("both_passed_count", sa.Integer()),
        sa.Column("both_failed_count", sa.Integer()),
        sa.Column("baseline_only_passed_count", sa.Integer()),
        sa.Column("candidate_only_passed_count", sa.Integer()),
        sa.Column("discordant_count", sa.Integer()),
        sa.Column("matched_pairs_odds_ratio", sa.Float()),
        sa.Column("p_value", sa.Float()),
        sa.Column("rejects_null", sa.Boolean()),
        sa.Column("source_interval_lower", sa.Float()),
        sa.Column("source_interval_upper", sa.Float()),
        sa.Column("confidence_interval_lower", sa.Float()),
        sa.Column("confidence_interval_upper", sa.Float()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["regression_check_id"], ["regression_checks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id", "condition_position"],
            [
                "experiment_condition_analyses.analysis_id",
                "experiment_condition_analyses.condition_position",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"], ["evaluation_definitions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("regression_check_id", "condition_position"),
        sa.CheckConstraint(classification, name="ck_regression_findings_classification"),
        sa.CheckConstraint(
            "baseline_variant IN ('A', 'B') AND candidate_variant IN ('A', 'B') "
            "AND baseline_variant <> candidate_variant",
            name="ck_regression_findings_orientation",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evaluator_config) = 'object'",
            name="ck_regression_findings_config",
        ),
        sa.CheckConstraint(
            "condition_position >= 0 AND condition_position < 20",
            name="ck_regression_findings_position",
        ),
        sa.CheckConstraint(
            "minimum_pass_rate_drop > 0 AND minimum_pass_rate_drop <= 1 "
            "AND minimum_sample_size >= 1 AND minimum_sample_size <= 1000",
            name="ck_regression_findings_policy_snapshot",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "sample_size IS NULL OR (sample_size >= 1 AND "
            "baseline_passed_count = both_passed_count + baseline_only_passed_count AND "
            "candidate_passed_count = both_passed_count + candidate_only_passed_count AND "
            "both_passed_count + both_failed_count + baseline_only_passed_count + "
            "candidate_only_passed_count = sample_size AND "
            "discordant_count = baseline_only_passed_count + candidate_only_passed_count)",
            name="ck_regression_findings_counts",
        ),
        sa.CheckConstraint(
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
    )
    op.create_index(
        "ix_regression_findings_definition",
        "regression_findings",
        ["definition_id", "created_at"],
    )


def downgrade() -> None:
    # Tolerates the policy-only development draft of this unreleased migration.
    op.execute("DROP TABLE IF EXISTS regression_findings")
    op.execute("DROP TABLE IF EXISTS regression_checks")
    op.drop_index("ix_regression_policies_created_id", table_name="regression_policies")
    op.drop_table("regression_policies")
