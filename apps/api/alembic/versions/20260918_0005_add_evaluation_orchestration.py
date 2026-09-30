"""Add durable evaluation subjects and orchestration metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260918_0005"
down_revision: str | None = "20260917_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evaluation_runs", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_evaluation_runs_attempt_count",
        "evaluation_runs",
        "attempt_count >= 0 AND attempt_count <= 4",
    )
    op.create_check_constraint(
        "ck_evaluation_runs_queue_time_order",
        "evaluation_runs",
        "(queued_at IS NULL OR queued_at >= created_at) AND "
        "(last_enqueued_at IS NULL OR "
        "(queued_at IS NOT NULL AND last_enqueued_at >= queued_at))",
    )
    op.create_index(
        "ix_evaluation_runs_recovery",
        "evaluation_runs",
        ["status", "last_enqueued_at", "started_at"],
    )
    op.create_table(
        "evaluation_run_subjects",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "position >= 0 AND position < 1000",
            name="ck_evaluation_run_subjects_position",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trace_id"], ["traces.trace_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("run_id", "trace_id"),
        sa.UniqueConstraint("run_id", "position", name="uq_evaluation_run_subjects_position"),
    )
    op.create_index("ix_evaluation_run_subjects_trace_id", "evaluation_run_subjects", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_run_subjects_trace_id", table_name="evaluation_run_subjects")
    op.drop_table("evaluation_run_subjects")
    op.drop_index("ix_evaluation_runs_recovery", table_name="evaluation_runs")
    op.drop_constraint("ck_evaluation_runs_queue_time_order", "evaluation_runs", type_="check")
    op.drop_constraint("ck_evaluation_runs_attempt_count", "evaluation_runs", type_="check")
    op.drop_column("evaluation_runs", "attempt_count")
    op.drop_column("evaluation_runs", "last_enqueued_at")
    op.drop_column("evaluation_runs", "queued_at")
