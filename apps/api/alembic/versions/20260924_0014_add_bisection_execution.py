"""Add durable bisection commit execution."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260924_0014"
down_revision: str | None = "20260924_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bisection_execution_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bisection_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "configuration_schema_version", sa.String(20), nullable=False, server_default="1"
        ),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("repository_fingerprint", sa.String(64), nullable=False),
        sa.Column("repository_root", sa.String(2048), nullable=False),
        sa.Column("planned_commit_count", sa.Integer(), nullable=False),
        sa.Column("requested_commit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_category", sa.String(60)),
        sa.Column("error_message", sa.String(4000)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("id", "session_id", name="uq_bisection_execution_runs_session"),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'completed', 'failed')",
            name="ck_bisection_execution_runs_status",
        ),
        sa.CheckConstraint(
            "configuration_schema_version = '1' AND jsonb_typeof(configuration) = 'object'",
            name="ck_bisection_execution_runs_configuration",
        ),
        sa.CheckConstraint(
            "repository_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_bisection_execution_runs_repository",
        ),
        sa.CheckConstraint(
            "planned_commit_count >= 1 AND planned_commit_count <= 5000 "
            "AND requested_commit_count >= 0 AND requested_commit_count <= 50",
            name="ck_bisection_execution_runs_counts",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND requested_commit_count = 0 AND queued_at IS NULL "
            "AND completed_at IS NULL AND error_category IS NULL) OR "
            "(status IN ('queued', 'running') AND requested_commit_count >= 1 "
            "AND queued_at IS NOT NULL AND completed_at IS NULL AND error_category IS NULL) OR "
            "(status = 'completed' AND requested_commit_count >= 1 "
            "AND completed_at IS NOT NULL AND error_category IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND error_category IS NOT NULL)",
            name="ck_bisection_execution_runs_lifecycle",
        ),
    )
    op.create_index(
        "ix_bisection_execution_runs_created", "bisection_execution_runs", ["created_at", "id"]
    )
    op.create_index(
        "ix_bisection_execution_runs_session_created",
        "bisection_execution_runs",
        ["session_id", "created_at", "id"],
    )
    op.create_index(
        "ix_bisection_execution_runs_recovery",
        "bisection_execution_runs",
        ["status", "last_enqueued_at"],
    )
    op.create_index(
        "uq_bisection_execution_runs_one_active",
        "bisection_execution_runs",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'queued', 'running')"),
    )

    op.create_table(
        "bisection_execution_targets",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("commit_sha", sa.String(40), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("commit_position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("outcome", sa.String(24)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("stdout", sa.Text(), nullable=False, server_default=""),
        sa.Column("stderr", sa.Text(), nullable=False, server_default=""),
        sa.Column("stdout_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stderr_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("timed_out", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("failure_kind", sa.String(60)),
        sa.Column("failure_message", sa.String(1000)),
        sa.Column("cleanup_failed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cleanup_message", sa.String(1000)),
        sa.ForeignKeyConstraint(
            ["run_id", "session_id"],
            ["bisection_execution_runs.id", "bisection_execution_runs.session_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "commit_sha"],
            ["bisection_commits.session_id", "bisection_commits.commit_sha"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'execution_failed')",
            name="ck_bisection_execution_targets_status",
        ),
        sa.CheckConstraint(
            "commit_sha ~ '^[0-9a-f]{40}$' AND commit_position >= 0 AND commit_position < 5000",
            name="ck_bisection_execution_targets_commit",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 3",
            name="ck_bisection_execution_targets_attempts",
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND outcome IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL AND finished_at IS NULL) OR "
            "(status = 'running' AND outcome IS NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND started_at IS NOT NULL AND finished_at IS NULL) OR "
            "(status = 'completed' AND outcome IN ('pass', 'regression', 'indeterminate') "
            "AND lease_token IS NULL AND lease_expires_at IS NULL AND finished_at IS NOT NULL) OR "
            "(status = 'execution_failed' AND outcome = 'execution_failed' "
            "AND lease_token IS NULL AND lease_expires_at IS NULL AND finished_at IS NOT NULL "
            "AND failure_kind IS NOT NULL)",
            name="ck_bisection_execution_targets_lifecycle",
        ),
        sa.CheckConstraint(
            "octet_length(stdout) <= 1048576 AND octet_length(stderr) <= 1048576",
            name="ck_bisection_execution_targets_output",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_bisection_execution_targets_duration",
        ),
    )
    op.create_index(
        "ix_bisection_execution_targets_recovery",
        "bisection_execution_targets",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "ix_bisection_execution_targets_run_position",
        "bisection_execution_targets",
        ["run_id", "commit_position"],
    )

    op.create_table(
        "bisection_execution_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("commit_sha", sa.String(40), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("outcome", sa.String(24)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("stdout", sa.Text(), nullable=False, server_default=""),
        sa.Column("stderr", sa.Text(), nullable=False, server_default=""),
        sa.Column("stdout_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stderr_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("timed_out", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("failure_kind", sa.String(60)),
        sa.Column("failure_message", sa.String(1000)),
        sa.Column("cleanup_failed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cleanup_message", sa.String(1000)),
        sa.ForeignKeyConstraint(
            ["run_id", "commit_sha"],
            ["bisection_execution_targets.run_id", "bisection_execution_targets.commit_sha"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "run_id", "commit_sha", "attempt_number", name="uq_bisection_execution_attempts_number"
        ),
        sa.UniqueConstraint("lease_token", name="uq_bisection_execution_attempts_lease"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_bisection_execution_attempts_status",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1 AND attempt_number <= 3",
            name="ck_bisection_execution_attempts_number",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND outcome IS NULL AND finished_at IS NULL) OR "
            "(status = 'completed' AND outcome IN ('pass', 'regression', 'indeterminate') "
            "AND finished_at IS NOT NULL AND failure_kind IS NULL) OR "
            "(status = 'failed' AND outcome = 'execution_failed' "
            "AND finished_at IS NOT NULL AND failure_kind IS NOT NULL)",
            name="ck_bisection_execution_attempts_lifecycle",
        ),
        sa.CheckConstraint(
            "octet_length(stdout) <= 1048576 AND octet_length(stderr) <= 1048576",
            name="ck_bisection_execution_attempts_output",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_bisection_attempts_duration"
        ),
    )
    op.create_index(
        "ix_bisection_execution_attempts_target",
        "bisection_execution_attempts",
        ["run_id", "commit_sha", "attempt_number"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bisection_execution_attempts_target", table_name="bisection_execution_attempts"
    )
    op.drop_table("bisection_execution_attempts")
    op.drop_index(
        "ix_bisection_execution_targets_run_position", table_name="bisection_execution_targets"
    )
    op.drop_index(
        "ix_bisection_execution_targets_recovery", table_name="bisection_execution_targets"
    )
    op.drop_table("bisection_execution_targets")
    op.drop_index("uq_bisection_execution_runs_one_active", table_name="bisection_execution_runs")
    op.drop_index("ix_bisection_execution_runs_recovery", table_name="bisection_execution_runs")
    op.drop_index(
        "ix_bisection_execution_runs_session_created", table_name="bisection_execution_runs"
    )
    op.drop_index("ix_bisection_execution_runs_created", table_name="bisection_execution_runs")
    op.drop_table("bisection_execution_runs")
