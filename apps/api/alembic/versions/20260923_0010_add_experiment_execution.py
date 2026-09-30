"""Add durable experiment runs and raw decisions."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260923_0010"
down_revision: str | None = "20260923_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "experiment_runs",
        sa.Column("id", uuid, nullable=False),
        sa.Column("experiment_id", uuid, nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("expected_decision_count", sa.Integer(), nullable=False),
        sa.Column("error_category", sa.String(40)),
        sa.Column("error_message", sa.String(4_000)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_token", uuid),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'completed', 'failed')",
            name="ck_experiment_runs_status",
        ),
        sa.CheckConstraint(
            "expected_decision_count >= 1 AND expected_decision_count <= 40000",
            name="ck_experiment_runs_expected_count",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 4",
            name="ck_experiment_runs_attempt_count",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_experiment_runs_lease",
        ),
        sa.CheckConstraint(
            "(status IN ('pending', 'queued', 'running') AND completed_at IS NULL "
            "AND error_category IS NULL AND error_message IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND error_category IS NULL AND error_message IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL "
            "AND error_category IS NOT NULL)",
            name="ck_experiment_runs_lifecycle",
        ),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "experiment_id", name="uq_experiment_runs_id_experiment"),
    )
    op.create_index("ix_experiment_runs_created_id", "experiment_runs", ["created_at", "id"])
    op.create_index(
        "ix_experiment_runs_experiment_created",
        "experiment_runs",
        ["experiment_id", "created_at", "id"],
    )
    op.create_index(
        "ix_experiment_runs_recovery",
        "experiment_runs",
        ["status", "lease_expires_at", "last_enqueued_at"],
    )
    op.create_index(
        "uq_experiment_runs_one_active",
        "experiment_runs",
        ["experiment_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'queued', 'running')"),
    )
    op.create_unique_constraint(
        "uq_experiment_subjects_identity",
        "experiment_subjects",
        ["experiment_id", "position", "variant_key", "trace_id"],
    )
    op.create_unique_constraint(
        "uq_experiment_conditions_identity",
        "experiment_evaluation_conditions",
        ["experiment_id", "position", "definition_id"],
    )

    op.create_table(
        "experiment_run_results",
        sa.Column("run_id", uuid, nullable=False),
        sa.Column("experiment_id", uuid, nullable=False),
        sa.Column("subject_position", sa.Integer(), nullable=False),
        sa.Column("variant_key", sa.String(1), nullable=False),
        sa.Column("condition_position", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("definition_id", uuid, nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("variant_key IN ('A', 'B')", name="ck_experiment_results_variant"),
        sa.CheckConstraint(
            "outcome IN ('passed', 'failed', 'error')", name="ck_experiment_results_outcome"
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="ck_experiment_results_score",
        ),
        sa.CheckConstraint(
            "outcome <> 'error' OR score IS NULL", name="ck_experiment_results_error_score"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'", name="ck_experiment_results_details"
        ),
        sa.CheckConstraint(
            "attempt_count >= 1 AND attempt_count <= 4",
            name="ck_experiment_results_attempt_count",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "experiment_id"],
            ["experiment_runs.id", "experiment_runs.experiment_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["experiment_id", "condition_position", "definition_id"],
            [
                "experiment_evaluation_conditions.experiment_id",
                "experiment_evaluation_conditions.position",
                "experiment_evaluation_conditions.definition_id",
            ],
            name="fk_experiment_results_condition_identity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "run_id", "subject_position", "variant_key", "condition_position"
        ),
    )
    op.create_index(
        "ix_experiment_results_order",
        "experiment_run_results",
        ["run_id", "subject_position", "variant_key", "condition_position"],
    )
    op.create_index(
        "ix_experiment_results_trace", "experiment_run_results", ["trace_id", "created_at"]
    )
    op.create_index(
        "ix_experiment_results_condition",
        "experiment_run_results",
        ["run_id", "condition_position"],
    )


def downgrade() -> None:
    op.drop_index("ix_experiment_results_condition", table_name="experiment_run_results")
    op.drop_index("ix_experiment_results_trace", table_name="experiment_run_results")
    op.drop_index("ix_experiment_results_order", table_name="experiment_run_results")
    op.drop_table("experiment_run_results")
    op.execute(
        "ALTER TABLE experiment_evaluation_conditions "
        "DROP CONSTRAINT IF EXISTS uq_experiment_conditions_identity"
    )
    op.execute(
        "ALTER TABLE experiment_subjects "
        "DROP CONSTRAINT IF EXISTS uq_experiment_subjects_identity"
    )
    op.drop_index("uq_experiment_runs_one_active", table_name="experiment_runs")
    op.drop_index("ix_experiment_runs_recovery", table_name="experiment_runs")
    op.drop_index("ix_experiment_runs_experiment_created", table_name="experiment_runs")
    op.drop_index("ix_experiment_runs_created_id", table_name="experiment_runs")
    op.drop_table("experiment_runs")
