"""Add canonical statistical calibration analyses."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260923_0008"
down_revision: str | None = "20260918_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "calibration_analyses",
        sa.Column("run_id", uuid, nullable=False),
        sa.Column("study_id", uuid, nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("human_passed_count", sa.Integer(), nullable=False),
        sa.Column("human_failed_count", sa.Integer(), nullable=False),
        sa.Column("judge_passed_count", sa.Integer(), nullable=False),
        sa.Column("judge_failed_count", sa.Integer(), nullable=False),
        sa.Column("agreement_count", sa.Integer(), nullable=False),
        sa.Column("disagreement_count", sa.Integer(), nullable=False),
        sa.Column("true_positive", sa.Integer(), nullable=False),
        sa.Column("true_negative", sa.Integer(), nullable=False),
        sa.Column("false_positive", sa.Integer(), nullable=False),
        sa.Column("false_negative", sa.Integer(), nullable=False),
        sa.Column("observed_agreement", sa.Float(), nullable=False),
        sa.Column("expected_agreement", sa.Float(), nullable=False),
        sa.Column("precision_passed", sa.Float()),
        sa.Column("recall_passed", sa.Float()),
        sa.Column("f1_passed", sa.Float()),
        sa.Column("specificity_failed", sa.Float()),
        sa.Column("cohens_kappa", sa.Float()),
        sa.Column("metric_schema_version", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sample_count >= 1 AND sample_count <= 500 AND "
            "human_passed_count >= 0 AND human_failed_count >= 0 AND "
            "judge_passed_count >= 0 AND judge_failed_count >= 0 AND "
            "agreement_count >= 0 AND disagreement_count >= 0 AND "
            "true_positive >= 0 AND true_negative >= 0 AND "
            "false_positive >= 0 AND false_negative >= 0",
            name="ck_calibration_analyses_nonnegative_counts",
        ),
        sa.CheckConstraint(
            "true_positive + true_negative + false_positive + false_negative = sample_count "
            "AND agreement_count = true_positive + true_negative "
            "AND disagreement_count = false_positive + false_negative "
            "AND agreement_count + disagreement_count = sample_count",
            name="ck_calibration_analyses_matrix_counts",
        ),
        sa.CheckConstraint(
            "human_passed_count = true_positive + false_negative "
            "AND human_failed_count = true_negative + false_positive "
            "AND judge_passed_count = true_positive + false_positive "
            "AND judge_failed_count = true_negative + false_negative",
            name="ck_calibration_analyses_marginal_counts",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "metric_schema_version = '1'", name="ck_calibration_analyses_schema_version"
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "study_id"],
            ["calibration_judge_runs.id", "calibration_judge_runs.study_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "ix_calibration_analyses_study_created",
        "calibration_analyses",
        ["study_id", "created_at", "run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_calibration_analyses_study_created", table_name="calibration_analyses")
    op.drop_table("calibration_analyses")
