"""Add production monitoring definitions and snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260925_0016"
down_revision: str | None = "20260924_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitoring_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(2000)),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("window_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("trace_name", sa.String(500)),
        sa.Column("trace_status", sa.String(20)),
        sa.Column(
            "evaluation_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evaluation_definitions.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "window_duration_seconds IN (300, 900, 3600, 21600, 86400)",
            name="ck_monitoring_definitions_duration",
        ),
        sa.CheckConstraint(
            "trace_status IS NULL OR trace_status IN ('unset', 'running', 'success', 'error')",
            name="ck_monitoring_definitions_trace_status",
        ),
    )
    op.create_index(
        "ix_monitoring_definitions_created", "monitoring_definitions", ["created_at", "id"]
    )
    op.create_index(
        "ix_monitoring_definitions_enabled", "monitoring_definitions", ["is_enabled", "id"]
    )

    op.create_table(
        "monitoring_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "monitoring_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("monitoring_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("trace_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successful_trace_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_trace_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_rate", sa.Float()),
        sa.Column("failure_rate", sa.Float()),
        sa.Column("duration_sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mean_duration_ms", sa.Float()),
        sa.Column("median_duration_ms", sa.Float()),
        sa.Column("p95_duration_ms", sa.Float()),
        sa.Column("token_sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.BigInteger()),
        sa.Column("output_tokens", sa.BigInteger()),
        sa.Column("total_tokens", sa.BigInteger()),
        sa.Column("mean_total_tokens", sa.Float()),
        sa.Column("evaluated_result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed_evaluation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_evaluation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evaluator_error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "valid_binary_evaluation_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("evaluation_pass_rate", sa.Float()),
        sa.Column("evaluation_error_rate", sa.Float()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.String(4000)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "monitoring_definition_id",
            "window_start",
            "window_end",
            name="uq_monitoring_snapshots_window",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'completed', 'failed')",
            name="ck_monitoring_snapshots_status",
        ),
        sa.CheckConstraint("window_end > window_start", name="ck_monitoring_snapshots_window"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 4", name="ck_monitoring_snapshots_attempts"
        ),
        sa.CheckConstraint(
            "trace_count >= 0 AND successful_trace_count >= 0 AND failed_trace_count >= 0 "
            "AND successful_trace_count + failed_trace_count <= trace_count "
            "AND duration_sample_count >= 0 AND duration_sample_count <= trace_count "
            "AND token_sample_count >= 0 AND token_sample_count <= trace_count",
            name="ck_monitoring_snapshots_trace_counts",
        ),
        sa.CheckConstraint(
            "evaluated_result_count >= 0 AND passed_evaluation_count >= 0 "
            "AND failed_evaluation_count >= 0 AND evaluator_error_count >= 0 "
            "AND valid_binary_evaluation_count = passed_evaluation_count + failed_evaluation_count "
            "AND evaluated_result_count = valid_binary_evaluation_count + evaluator_error_count",
            name="ck_monitoring_snapshots_evaluation_counts",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_monitoring_snapshots_lease",
        ),
        sa.CheckConstraint(
            "(status IN ('pending', 'queued', 'running') AND completed_at IS NULL "
            "AND error_message IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND error_message IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND error_message IS NOT NULL)",
            name="ck_monitoring_snapshots_lifecycle",
        ),
        sa.CheckConstraint(
            "(trace_count = 0 AND success_rate IS NULL AND failure_rate IS NULL) OR "
            "(trace_count > 0 AND success_rate BETWEEN 0 AND 1 AND failure_rate BETWEEN 0 AND 1)",
            name="ck_monitoring_snapshots_trace_rates",
        ),
        sa.CheckConstraint(
            "(valid_binary_evaluation_count = 0 AND evaluation_pass_rate IS NULL) OR "
            "(valid_binary_evaluation_count > 0 AND evaluation_pass_rate BETWEEN 0 AND 1)",
            name="ck_monitoring_snapshots_pass_rate",
        ),
        sa.CheckConstraint(
            "(evaluated_result_count = 0 AND evaluation_error_rate IS NULL) OR "
            "(evaluated_result_count > 0 AND evaluation_error_rate BETWEEN 0 AND 1)",
            name="ck_monitoring_snapshots_error_rate",
        ),
    )
    op.create_index(
        "ix_monitoring_snapshots_definition_window",
        "monitoring_snapshots",
        ["monitoring_definition_id", "window_start"],
    )
    op.create_index(
        "ix_monitoring_snapshots_recovery",
        "monitoring_snapshots",
        ["status", "last_enqueued_at", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_monitoring_snapshots_recovery", table_name="monitoring_snapshots")
    op.drop_index("ix_monitoring_snapshots_definition_window", table_name="monitoring_snapshots")
    op.drop_table("monitoring_snapshots")
    op.drop_index("ix_monitoring_definitions_enabled", table_name="monitoring_definitions")
    op.drop_index("ix_monitoring_definitions_created", table_name="monitoring_definitions")
    op.drop_table("monitoring_definitions")
