"""Add canonical paired experiment analyses."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260923_0011"
down_revision: str | None = "20260923_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "experiment_run_analyses",
        sa.Column("id", uuid, nullable=False),
        sa.Column("run_id", uuid, nullable=False),
        sa.Column("experiment_id", uuid, nullable=False),
        sa.Column("analysis_schema_version", sa.String(20), nullable=False),
        sa.Column("confidence_method", sa.String(60), nullable=False),
        sa.Column("confidence_level", sa.Float(), nullable=False),
        sa.Column("bootstrap_seed", sa.Integer(), nullable=False),
        sa.Column("bootstrap_iterations", sa.Integer(), nullable=False),
        sa.Column("hypothesis_test_method", sa.String(60), nullable=False),
        sa.Column("alpha", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "analysis_schema_version = '1'", name="ck_experiment_run_analyses_schema"
        ),
        sa.CheckConstraint(
            "confidence_method = 'paired_percentile_bootstrap' "
            "AND confidence_level > 0 AND confidence_level < 1 "
            "AND bootstrap_iterations >= 1 AND bootstrap_iterations <= 1000000",
            name="ck_experiment_run_analyses_confidence",
        ),
        sa.CheckConstraint(
            "hypothesis_test_method = 'exact_two_sided_mcnemar_binomial' "
            "AND alpha > 0 AND alpha < 1",
            name="ck_experiment_run_analyses_test",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "experiment_id"],
            ["experiment_runs.id", "experiment_runs.experiment_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_experiment_run_analyses_run"),
        sa.UniqueConstraint(
            "id", "experiment_id", name="uq_experiment_run_analyses_identity"
        ),
    )
    op.create_table(
        "experiment_condition_analyses",
        sa.Column("analysis_id", uuid, nullable=False),
        sa.Column("experiment_id", uuid, nullable=False),
        sa.Column("condition_position", sa.Integer(), nullable=False),
        sa.Column("definition_id", uuid, nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("ineligible_reason", sa.String(60)),
        sa.Column("sample_size", sa.Integer()),
        sa.Column("a_passed_count", sa.Integer()),
        sa.Column("a_failed_count", sa.Integer()),
        sa.Column("b_passed_count", sa.Integer()),
        sa.Column("b_failed_count", sa.Integer()),
        sa.Column("a_pass_rate", sa.Float()),
        sa.Column("b_pass_rate", sa.Float()),
        sa.Column("pass_rate_difference", sa.Float()),
        sa.Column("both_passed_count", sa.Integer()),
        sa.Column("both_passed_rate", sa.Float()),
        sa.Column("both_failed_count", sa.Integer()),
        sa.Column("both_failed_rate", sa.Float()),
        sa.Column("a_only_passed_count", sa.Integer()),
        sa.Column("a_only_passed_rate", sa.Float()),
        sa.Column("b_only_passed_count", sa.Integer()),
        sa.Column("b_only_passed_rate", sa.Float()),
        sa.Column("matched_pairs_odds_ratio", sa.Float()),
        sa.Column("confidence_interval_lower", sa.Float()),
        sa.Column("confidence_interval_upper", sa.Float()),
        sa.Column("discordant_count", sa.Integer()),
        sa.Column("p_value", sa.Float()),
        sa.Column("rejects_null", sa.Boolean()),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
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
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(
            ["analysis_id", "experiment_id"],
            ["experiment_run_analyses.id", "experiment_run_analyses.experiment_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id", "condition_position", "definition_id"],
            [
                "experiment_evaluation_conditions.experiment_id",
                "experiment_evaluation_conditions.position",
                "experiment_evaluation_conditions.definition_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("analysis_id", "condition_position"),
    )


def downgrade() -> None:
    op.drop_table("experiment_condition_analyses")
    op.drop_table("experiment_run_analyses")
