"""Add immutable Git bisection planning evidence."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260924_0013"
down_revision: str | None = "20260924_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "bisection_sessions",
        sa.Column("id", uuid, nullable=False),
        sa.Column("regression_check_id", uuid, nullable=False),
        sa.Column("repository_path", sa.String(2_048), nullable=False),
        sa.Column("repository_root", sa.String(2_048), nullable=False),
        sa.Column("repository_common_dir", sa.String(2_048), nullable=False),
        sa.Column("repository_fingerprint", sa.String(64), nullable=False),
        sa.Column("repository_head_sha", sa.String(40), nullable=False),
        sa.Column("baseline_revision", sa.String(200), nullable=False),
        sa.Column("candidate_revision", sa.String(200), nullable=False),
        sa.Column("baseline_commit_sha", sa.String(40), nullable=False),
        sa.Column("candidate_commit_sha", sa.String(40), nullable=False),
        sa.Column("baseline_provenance", postgresql.JSONB(), nullable=False),
        sa.Column("candidate_provenance", postgresql.JSONB(), nullable=False),
        sa.Column("commit_count", sa.Integer(), nullable=False),
        sa.Column("maximum_commit_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["regression_check_id"], ["regression_checks.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "regression_check_id",
            "repository_fingerprint",
            "baseline_commit_sha",
            "candidate_commit_sha",
            name="uq_bisection_sessions_plan",
        ),
        sa.CheckConstraint("status = 'ready'", name="ck_bisection_sessions_status"),
        sa.CheckConstraint(
            "baseline_commit_sha ~ '^[0-9a-f]{40}$' "
            "AND candidate_commit_sha ~ '^[0-9a-f]{40}$' "
            "AND repository_head_sha ~ '^[0-9a-f]{40}$' "
            "AND baseline_commit_sha <> candidate_commit_sha",
            name="ck_bisection_sessions_shas",
        ),
        sa.CheckConstraint(
            "repository_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_bisection_sessions_fingerprint",
        ),
        sa.CheckConstraint(
            "commit_count >= 1 AND commit_count <= maximum_commit_count "
            "AND maximum_commit_count >= 1 AND maximum_commit_count <= 5000",
            name="ck_bisection_sessions_commit_count",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(baseline_provenance) = 'object' "
            "AND jsonb_typeof(candidate_provenance) = 'object'",
            name="ck_bisection_sessions_provenance",
        ),
    )
    op.create_index("ix_bisection_sessions_created_id", "bisection_sessions", ["created_at", "id"])
    op.create_index(
        "ix_bisection_sessions_regression_check",
        "bisection_sessions",
        ["regression_check_id", "created_at"],
    )
    op.create_table(
        "bisection_commits",
        sa.Column("session_id", uuid, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("commit_sha", sa.String(40), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("parent_shas", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["bisection_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "position"),
        sa.UniqueConstraint("session_id", "commit_sha", name="uq_bisection_commits_sha"),
        sa.CheckConstraint(
            "position >= 0 AND position < 5000",
            name="ck_bisection_commits_position",
        ),
        sa.CheckConstraint("commit_sha ~ '^[0-9a-f]{40}$'", name="ck_bisection_commits_sha"),
        sa.CheckConstraint(
            "jsonb_typeof(parent_shas) = 'array'",
            name="ck_bisection_commits_parents",
        ),
    )
    op.create_index("ix_bisection_commits_sha", "bisection_commits", ["commit_sha"])


def downgrade() -> None:
    op.drop_index("ix_bisection_commits_sha", table_name="bisection_commits")
    op.drop_table("bisection_commits")
    op.drop_index("ix_bisection_sessions_regression_check", table_name="bisection_sessions")
    op.drop_index("ix_bisection_sessions_created_id", table_name="bisection_sessions")
    op.drop_table("bisection_sessions")
