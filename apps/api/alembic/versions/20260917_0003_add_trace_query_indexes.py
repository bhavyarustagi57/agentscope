"""Add indexes for trace query pagination and span-kind filters."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260917_0003"
down_revision: str | None = "20260917_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_traces_started_trace_id", "traces", ["started_at", "trace_id"])
    op.create_index("ix_spans_trace_kind", "spans", ["trace_id", "kind"])


def downgrade() -> None:
    op.drop_index("ix_spans_trace_kind", table_name="spans")
    op.drop_index("ix_traces_started_trace_id", table_name="traces")
