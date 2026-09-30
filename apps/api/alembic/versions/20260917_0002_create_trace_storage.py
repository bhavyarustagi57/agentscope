"""Create trace and span storage."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260917_0002"
down_revision: str | None = "20260916_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "traces",
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_traces_duration"),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name="ck_traces_time_order"
        ),
        sa.PrimaryKeyConstraint("trace_id"),
    )
    op.create_index("ix_traces_name", "traces", ["name"])
    op.create_index("ix_traces_started_at", "traces", ["started_at"])
    op.create_index("ix_traces_status", "traces", ["status"])

    op.create_table(
        "spans",
        sa.Column("span_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("parent_span_id", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("events", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("llm", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "parent_span_id IS NULL OR parent_span_id <> span_id",
            name="ck_spans_not_self_parent",
        ),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_spans_duration"),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name="ck_spans_time_order"
        ),
        sa.ForeignKeyConstraint(["trace_id"], ["traces.trace_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["trace_id", "parent_span_id"],
            ["spans.trace_id", "spans.span_id"],
            name="fk_spans_parent_same_trace",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("span_id"),
        sa.UniqueConstraint("trace_id", "span_id", name="uq_spans_trace_span"),
    )
    op.create_index("ix_spans_kind", "spans", ["kind"])
    op.create_index("ix_spans_started_at", "spans", ["started_at"])
    op.create_index("ix_spans_status", "spans", ["status"])
    op.create_index("ix_spans_trace_parent", "spans", ["trace_id", "parent_span_id"])


def downgrade() -> None:
    op.drop_index("ix_spans_trace_parent", table_name="spans")
    op.drop_index("ix_spans_status", table_name="spans")
    op.drop_index("ix_spans_started_at", table_name="spans")
    op.drop_index("ix_spans_kind", table_name="spans")
    op.drop_table("spans")
    op.drop_index("ix_traces_status", table_name="traces")
    op.drop_index("ix_traces_started_at", table_name="traces")
    op.drop_index("ix_traces_name", table_name="traces")
    op.drop_table("traces")
