from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from agentscope_api.database import Base


class MonitoringDefinitionRecord(Base):
    __tablename__ = "monitoring_definitions"
    __table_args__ = (
        CheckConstraint(
            "window_duration_seconds IN (300, 900, 3600, 21600, 86400)",
            name="ck_monitoring_definitions_duration",
        ),
        CheckConstraint(
            "trace_status IS NULL OR trace_status IN ('unset', 'running', 'success', 'error')",
            name="ck_monitoring_definitions_trace_status",
        ),
        Index("ix_monitoring_definitions_created", "created_at", "id"),
        Index("ix_monitoring_definitions_enabled", "is_enabled", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2_000))
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    window_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    trace_name: Mapped[str | None] = mapped_column(String(500))
    trace_status: Mapped[str | None] = mapped_column(String(20))
    evaluation_definition_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("evaluation_definitions.id", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MonitoringSnapshotRecord(Base):
    __tablename__ = "monitoring_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "monitoring_definition_id",
            "window_start",
            "window_end",
            name="uq_monitoring_snapshots_window",
        ),
        CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'completed', 'failed')",
            name="ck_monitoring_snapshots_status",
        ),
        CheckConstraint("window_end > window_start", name="ck_monitoring_snapshots_window"),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 4", name="ck_monitoring_snapshots_attempts"
        ),
        CheckConstraint(
            "trace_count >= 0 AND successful_trace_count >= 0 AND failed_trace_count >= 0 "
            "AND successful_trace_count + failed_trace_count <= trace_count "
            "AND duration_sample_count >= 0 AND duration_sample_count <= trace_count "
            "AND token_sample_count >= 0 AND token_sample_count <= trace_count",
            name="ck_monitoring_snapshots_trace_counts",
        ),
        CheckConstraint(
            "evaluated_result_count >= 0 AND passed_evaluation_count >= 0 "
            "AND failed_evaluation_count >= 0 AND evaluator_error_count >= 0 "
            "AND valid_binary_evaluation_count = passed_evaluation_count + failed_evaluation_count "
            "AND evaluated_result_count = valid_binary_evaluation_count + evaluator_error_count",
            name="ck_monitoring_snapshots_evaluation_counts",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_monitoring_snapshots_lease",
        ),
        CheckConstraint(
            "(status IN ('pending', 'queued', 'running') AND completed_at IS NULL "
            "AND error_message IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND error_message IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND error_message IS NOT NULL)",
            name="ck_monitoring_snapshots_lifecycle",
        ),
        CheckConstraint(
            "(trace_count = 0 AND success_rate IS NULL AND failure_rate IS NULL) OR "
            "(trace_count > 0 AND success_rate BETWEEN 0 AND 1 AND failure_rate BETWEEN 0 AND 1)",
            name="ck_monitoring_snapshots_trace_rates",
        ),
        CheckConstraint(
            "(valid_binary_evaluation_count = 0 AND evaluation_pass_rate IS NULL) OR "
            "(valid_binary_evaluation_count > 0 AND evaluation_pass_rate BETWEEN 0 AND 1)",
            name="ck_monitoring_snapshots_pass_rate",
        ),
        CheckConstraint(
            "(evaluated_result_count = 0 AND evaluation_error_rate IS NULL) OR "
            "(evaluated_result_count > 0 AND evaluation_error_rate BETWEEN 0 AND 1)",
            name="ck_monitoring_snapshots_error_rate",
        ),
        Index(
            "ix_monitoring_snapshots_definition_window", "monitoring_definition_id", "window_start"
        ),
        Index("ix_monitoring_snapshots_recovery", "status", "last_enqueued_at", "lease_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    monitoring_definition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    trace_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    successful_trace_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failed_trace_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    success_rate: Mapped[float | None] = mapped_column(Float)
    failure_rate: Mapped[float | None] = mapped_column(Float)
    duration_sample_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    mean_duration_ms: Mapped[float | None] = mapped_column(Float)
    median_duration_ms: Mapped[float | None] = mapped_column(Float)
    p95_duration_ms: Mapped[float | None] = mapped_column(Float)
    token_sample_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger)
    mean_total_tokens: Mapped[float | None] = mapped_column(Float)
    evaluated_result_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    passed_evaluation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failed_evaluation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    evaluator_error_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    valid_binary_evaluation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    evaluation_pass_rate: Mapped[float | None] = mapped_column(Float)
    evaluation_error_rate: Mapped[float | None] = mapped_column(Float)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(String(4_000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
