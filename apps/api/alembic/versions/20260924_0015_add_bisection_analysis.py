"""Add durable automated bisection analysis."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260924_0015"
down_revision: str | None = "20260924_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bisection_analyses",
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
        sa.Column("baseline_commit_sha", sa.String(40), nullable=False),
        sa.Column("candidate_commit_sha", sa.String(40), nullable=False),
        sa.Column("total_commit_count", sa.Integer(), nullable=False),
        sa.Column("good_position", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("good_commit_sha", sa.String(40), nullable=False),
        sa.Column("bad_position", sa.Integer(), nullable=False),
        sa.Column("bad_commit_sha", sa.String(40), nullable=False),
        sa.Column("waiting_position", sa.Integer()),
        sa.Column("waiting_commit_sha", sa.String(40)),
        sa.Column(
            "waiting_execution_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bisection_execution_runs.id", ondelete="RESTRICT"),
        ),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reused_evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("indeterminate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("execution_failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("final_good_commit_sha", sa.String(40)),
        sa.Column("final_good_position", sa.Integer()),
        sa.Column("final_bad_commit_sha", sa.String(40)),
        sa.Column("final_bad_position", sa.Integer()),
        sa.Column("final_good_snapshot", postgresql.JSONB()),
        sa.Column("final_bad_snapshot", postgresql.JSONB()),
        sa.Column("terminal_reason", sa.String(60)),
        sa.Column("terminal_message", sa.String(1000)),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("id", "session_id", name="uq_bisection_analyses_session"),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'waiting', "
            "'attributed', 'inconclusive', 'failed')",
            name="ck_bisection_analyses_status",
        ),
        sa.CheckConstraint(
            "configuration_schema_version = '1' AND jsonb_typeof(configuration) = 'object'",
            name="ck_bisection_analyses_configuration",
        ),
        sa.CheckConstraint(
            "baseline_commit_sha ~ '^[0-9a-f]{40}$' AND candidate_commit_sha ~ '^[0-9a-f]{40}$' "
            "AND good_commit_sha ~ '^[0-9a-f]{40}$' AND bad_commit_sha ~ '^[0-9a-f]{40}$'",
            name="ck_bisection_analyses_shas",
        ),
        sa.CheckConstraint(
            "total_commit_count >= 1 AND total_commit_count <= 5000 "
            "AND good_position >= -1 AND bad_position >= 0 "
            "AND good_position < bad_position AND bad_position < total_commit_count",
            name="ck_bisection_analyses_interval",
        ),
        sa.CheckConstraint(
            "step_count >= 0 AND reused_evidence_count >= 0 AND new_evidence_count >= 0 "
            "AND indeterminate_count >= 0 AND execution_failure_count >= 0",
            name="ck_bisection_analyses_counts",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_bisection_analyses_lease",
        ),
        sa.CheckConstraint(
            "(status = 'waiting' AND waiting_commit_sha IS NOT NULL "
            "AND waiting_position IS NOT NULL AND waiting_execution_run_id IS NOT NULL) OR "
            "(status <> 'waiting')",
            name="ck_bisection_analyses_waiting",
        ),
        sa.CheckConstraint(
            "(status = 'attributed' AND completed_at IS NOT NULL "
            "AND final_good_commit_sha IS NOT NULL AND final_bad_commit_sha IS NOT NULL "
            "AND final_good_snapshot IS NOT NULL AND final_bad_snapshot IS NOT NULL "
            "AND terminal_reason IS NULL) OR "
            "(status IN ('inconclusive', 'failed') AND completed_at IS NOT NULL "
            "AND terminal_reason IS NOT NULL) OR "
            "(status IN ('pending', 'queued', 'running', 'waiting') AND completed_at IS NULL "
            "AND final_good_commit_sha IS NULL AND final_bad_commit_sha IS NULL)",
            name="ck_bisection_analyses_terminal",
        ),
    )
    op.create_index("ix_bisection_analyses_created", "bisection_analyses", ["created_at", "id"])
    op.create_index(
        "ix_bisection_analyses_session_created",
        "bisection_analyses",
        ["session_id", "created_at", "id"],
    )
    op.create_index(
        "ix_bisection_analyses_recovery",
        "bisection_analyses",
        ["status", "last_enqueued_at", "lease_expires_at"],
    )
    op.create_index(
        "uq_bisection_analyses_one_active",
        "bisection_analyses",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'queued', 'running', 'waiting')"),
    )

    op.create_table(
        "bisection_analysis_steps",
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sequence_number", sa.Integer(), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("good_position_before", sa.Integer(), nullable=False),
        sa.Column("good_commit_sha_before", sa.String(40), nullable=False),
        sa.Column("bad_position_before", sa.Integer(), nullable=False),
        sa.Column("bad_commit_sha_before", sa.String(40), nullable=False),
        sa.Column("selected_position", sa.Integer(), nullable=False),
        sa.Column("selected_commit_sha", sa.String(40), nullable=False),
        sa.Column("evidence_source", sa.String(20), nullable=False),
        sa.Column(
            "execution_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bisection_execution_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("observed_outcome", sa.String(24), nullable=False),
        sa.Column("decision", sa.String(40), nullable=False),
        sa.Column("good_position_after", sa.Integer(), nullable=False),
        sa.Column("good_commit_sha_after", sa.String(40), nullable=False),
        sa.Column("bad_position_after", sa.Integer(), nullable=False),
        sa.Column("bad_commit_sha_after", sa.String(40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id", "session_id"],
            ["bisection_analyses.id", "bisection_analyses.session_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "selected_commit_sha"],
            ["bisection_commits.session_id", "bisection_commits.commit_sha"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "analysis_id", "selected_position", name="uq_bisection_analysis_steps_position"
        ),
        sa.CheckConstraint(
            "sequence_number >= 1 AND sequence_number <= 5001",
            name="ck_bisection_analysis_steps_sequence",
        ),
        sa.CheckConstraint(
            "evidence_source IN ('reused', 'requested')",
            name="ck_bisection_analysis_steps_source",
        ),
        sa.CheckConstraint(
            "observed_outcome IN ('pass', 'regression', 'indeterminate', 'execution_failed')",
            name="ck_bisection_analysis_steps_outcome",
        ),
        sa.CheckConstraint(
            "decision IN ('confirm_candidate', 'advance_good', 'retreat_bad', "
            "'skip_indeterminate', 'skip_execution_failed')",
            name="ck_bisection_analysis_steps_decision",
        ),
        sa.CheckConstraint(
            "good_position_before >= -1 AND bad_position_before >= 0 "
            "AND good_position_before < bad_position_before "
            "AND selected_position >= 0 AND good_position_after >= -1 "
            "AND bad_position_after >= 0 AND good_position_after < bad_position_after",
            name="ck_bisection_analysis_steps_positions",
        ),
    )
    op.create_index(
        "ix_bisection_analysis_steps_selected",
        "bisection_analysis_steps",
        ["session_id", "selected_commit_sha"],
    )


def downgrade() -> None:
    op.drop_index("ix_bisection_analysis_steps_selected", table_name="bisection_analysis_steps")
    op.drop_table("bisection_analysis_steps")
    op.drop_index("uq_bisection_analyses_one_active", table_name="bisection_analyses")
    op.drop_index("ix_bisection_analyses_recovery", table_name="bisection_analyses")
    op.drop_index("ix_bisection_analyses_session_created", table_name="bisection_analyses")
    op.drop_index("ix_bisection_analyses_created", table_name="bisection_analyses")
    op.drop_table("bisection_analyses")
