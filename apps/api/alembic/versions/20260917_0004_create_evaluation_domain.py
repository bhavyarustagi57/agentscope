"""Create evaluation definitions, runs, and trace-linked results."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260917_0004"
down_revision: str | None = "20260917_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2_000), nullable=True),
        sa.Column("evaluator_kind", sa.String(length=40), nullable=False),
        sa.Column("evaluator_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evaluator_config) = 'object'",
            name="ck_evaluation_definitions_config_object",
        ),
        sa.CheckConstraint(
            "evaluator_kind IN ('exact_match', 'contains', 'numeric_threshold')",
            name="ck_evaluation_definitions_kind",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evaluation_definitions_created_id",
        "evaluation_definitions",
        ["created_at", "id"],
    )
    op.create_index("ix_evaluation_definitions_enabled", "evaluation_definitions", ["is_enabled"])
    op.create_index("ix_evaluation_definitions_kind", "evaluation_definitions", ["evaluator_kind"])

    op.create_table(
        "evaluation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("definition_name", sa.String(length=200), nullable=False),
        sa.Column("evaluator_kind", sa.String(length=40), nullable=False),
        sa.Column("evaluator_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("error_message", sa.String(length=4_000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "jsonb_typeof(evaluator_config) = 'object'",
            name="ck_evaluation_runs_config_object",
        ),
        sa.CheckConstraint(
            "evaluator_kind IN ('exact_match', 'contains', 'numeric_threshold')",
            name="ck_evaluation_runs_kind",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at)",
            name="ck_evaluation_runs_completion_order",
        ),
        sa.CheckConstraint(
            "(status IN ('pending', 'queued') AND started_at IS NULL AND completed_at IS NULL "
            "AND error_message IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND error_message IS NULL) OR "
            "(status = 'completed' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND error_message IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL)",
            name="ck_evaluation_runs_lifecycle",
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="ck_evaluation_runs_start_order",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'completed', 'failed')",
            name="ck_evaluation_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"], ["evaluation_definitions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evaluation_runs_created_id", "evaluation_runs", ["created_at", "id"])
    op.create_index(
        "ix_evaluation_runs_definition_created",
        "evaluation_runs",
        ["definition_id", "created_at", "id"],
    )
    op.create_index(
        "ix_evaluation_runs_status_created",
        "evaluation_runs",
        ["status", "created_at", "id"],
    )

    op.create_table(
        "evaluation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'", name="ck_evaluation_results_details_object"
        ),
        sa.CheckConstraint(
            "outcome <> 'error' OR score IS NULL", name="ck_evaluation_results_error_score"
        ),
        sa.CheckConstraint(
            "outcome IN ('passed', 'failed', 'error')",
            name="ck_evaluation_results_outcome",
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="ck_evaluation_results_score",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trace_id"], ["traces.trace_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "trace_id", name="uq_evaluation_results_run_trace"),
    )
    op.create_index(
        "ix_evaluation_results_run_created",
        "evaluation_results",
        ["run_id", "created_at", "id"],
    )
    op.create_index(
        "ix_evaluation_results_trace_created",
        "evaluation_results",
        ["trace_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_results_trace_created", table_name="evaluation_results")
    op.drop_index("ix_evaluation_results_run_created", table_name="evaluation_results")
    op.drop_table("evaluation_results")
    op.drop_index("ix_evaluation_runs_status_created", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_definition_created", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_created_id", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
    op.drop_index("ix_evaluation_definitions_kind", table_name="evaluation_definitions")
    op.drop_index("ix_evaluation_definitions_enabled", table_name="evaluation_definitions")
    op.drop_index("ix_evaluation_definitions_created_id", table_name="evaluation_definitions")
    op.drop_table("evaluation_definitions")
