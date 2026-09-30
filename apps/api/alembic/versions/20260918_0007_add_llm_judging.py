"""Add durable LLM judge execution."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260918_0007"
down_revision: str | None = "20260918_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "judge_configurations",
        sa.Column("id", uuid, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(2_000)),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("rubric", sa.String(8_000), nullable=False),
        sa.Column("output_schema_version", sa.String(20), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("configuration_version", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("provider = 'openai'", name="ck_judge_configurations_provider"),
        sa.CheckConstraint("output_schema_version = '1'", name="ck_judge_configurations_schema"),
        sa.CheckConstraint("configuration_version = '1'", name="ck_judge_configurations_version"),
        sa.CheckConstraint(
            "timeout_seconds >= 5 AND timeout_seconds <= 300",
            name="ck_judge_configurations_timeout",
        ),
        sa.CheckConstraint(
            "max_output_tokens >= 32 AND max_output_tokens <= 1000",
            name="ck_judge_configurations_tokens",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_judge_configurations_created_id", "judge_configurations", ["created_at", "id"]
    )

    op.create_table(
        "calibration_judge_runs",
        sa.Column("id", uuid, nullable=False),
        sa.Column("study_id", uuid, nullable=False),
        sa.Column("configuration_id", uuid, nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("rubric", sa.String(8_000), nullable=False),
        sa.Column("output_schema_version", sa.String(20), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("configuration_version", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("error_category", sa.String(40)),
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
            name="ck_judge_runs_status",
        ),
        sa.CheckConstraint("provider = 'openai'", name="ck_judge_runs_provider"),
        sa.CheckConstraint("output_schema_version = '1'", name="ck_judge_runs_schema"),
        sa.CheckConstraint("configuration_version = '1'", name="ck_judge_runs_version"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 3", name="ck_judge_runs_attempts"
        ),
        sa.CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_judge_runs_lease",
        ),
        sa.CheckConstraint(
            "(status IN ('pending', 'queued') AND completed_at IS NULL "
            "AND error_category IS NULL) OR "
            "(status = 'running' AND completed_at IS NULL AND error_category IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND error_category IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL "
            "AND error_category IS NOT NULL)",
            name="ck_judge_runs_lifecycle",
        ),
        sa.ForeignKeyConstraint(["study_id"], ["calibration_studies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["configuration_id"], ["judge_configurations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "study_id", name="uq_judge_runs_id_study"),
    )
    op.create_index("ix_judge_runs_created_id", "calibration_judge_runs", ["created_at", "id"])
    op.create_index(
        "ix_judge_runs_study_created", "calibration_judge_runs", ["study_id", "created_at", "id"]
    )
    op.create_index(
        "ix_judge_runs_recovery",
        "calibration_judge_runs",
        ["status", "lease_expires_at", "last_enqueued_at"],
    )

    op.create_table(
        "calibration_judge_results",
        sa.Column("run_id", uuid, nullable=False),
        sa.Column("study_id", uuid, nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("decision", sa.String(20)),
        sa.Column("rationale", sa.String(4_000)),
        sa.Column("error_category", sa.String(40)),
        sa.Column("provider_request_id", sa.String(200)),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("latency_ms", sa.Float()),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(decision IN ('passed', 'failed') AND rationale IS NOT NULL "
            "AND error_category IS NULL) OR "
            "(decision IS NULL AND rationale IS NULL AND error_category IS NOT NULL)",
            name="ck_judge_results_outcome",
        ),
        sa.CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) AND "
            "(output_tokens IS NULL OR output_tokens >= 0) AND "
            "(total_tokens IS NULL OR total_tokens >= 0)",
            name="ck_judge_results_tokens",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0", name="ck_judge_results_latency"
        ),
        sa.CheckConstraint(
            "attempt_count >= 1 AND attempt_count <= 3", name="ck_judge_results_attempt"
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "study_id"],
            ["calibration_judge_runs.id", "calibration_judge_runs.study_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["study_id", "trace_id"],
            ["calibration_study_subjects.study_id", "calibration_study_subjects.trace_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_id", "trace_id"),
    )
    op.create_index(
        "ix_judge_results_run_created",
        "calibration_judge_results",
        ["run_id", "created_at", "trace_id"],
    )
    op.create_index(
        "ix_judge_results_trace", "calibration_judge_results", ["trace_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_judge_results_trace", table_name="calibration_judge_results")
    op.drop_index("ix_judge_results_run_created", table_name="calibration_judge_results")
    op.drop_table("calibration_judge_results")
    op.drop_index("ix_judge_runs_recovery", table_name="calibration_judge_runs")
    op.drop_index("ix_judge_runs_study_created", table_name="calibration_judge_runs")
    op.drop_index("ix_judge_runs_created_id", table_name="calibration_judge_runs")
    op.drop_table("calibration_judge_runs")
    op.drop_index("ix_judge_configurations_created_id", table_name="judge_configurations")
    op.drop_table("judge_configurations")
