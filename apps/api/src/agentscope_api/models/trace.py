from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentscope_api.database import Base


class TraceRecord(Base):
    __tablename__ = "traces"
    __table_args__ = (
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_traces_duration"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_traces_time_order"),
        Index("ix_traces_started_at", "started_at"),
        Index("ix_traces_started_trace_id", "started_at", "trace_id"),
        Index("ix_traces_status", "status"),
        Index("ix_traces_name", "name"),
    )

    trace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float | None] = mapped_column(Float)
    input: Mapped[Any] = mapped_column(JSONB)
    output: Mapped[Any] = mapped_column(JSONB)
    error: Mapped[Any] = mapped_column(JSONB)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    tags: Mapped[list[str]] = mapped_column(JSONB)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SpanRecord(Base):
    __tablename__ = "spans"
    __table_args__ = (
        UniqueConstraint("trace_id", "span_id", name="uq_spans_trace_span"),
        ForeignKeyConstraint(
            ["trace_id", "parent_span_id"],
            ["spans.trace_id", "spans.span_id"],
            name="fk_spans_parent_same_trace",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "parent_span_id IS NULL OR parent_span_id <> span_id",
            name="ck_spans_not_self_parent",
        ),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_spans_duration"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_spans_time_order"),
        Index("ix_spans_trace_parent", "trace_id", "parent_span_id"),
        Index("ix_spans_trace_kind", "trace_id", "kind"),
        Index("ix_spans_kind", "kind"),
        Index("ix_spans_status", "status"),
        Index("ix_spans_started_at", "started_at"),
    )

    span_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("traces.trace_id", ondelete="CASCADE"), nullable=False
    )
    parent_span_id: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float | None] = mapped_column(Float)
    input: Mapped[Any] = mapped_column(JSONB)
    output: Mapped[Any] = mapped_column(JSONB)
    error: Mapped[Any] = mapped_column(JSONB)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB)
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    llm: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
