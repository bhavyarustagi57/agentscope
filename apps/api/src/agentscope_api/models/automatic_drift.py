from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from agentscope_api.database import Base


class AutomaticDriftConfigurationRecord(Base):
    __tablename__ = "automatic_drift_configurations"
    __table_args__ = (
        CheckConstraint(
            "baseline_strategy = 'previous_window'", name="ck_auto_drift_config_strategy"
        ),
        CheckConstraint(
            "cooldown_seconds >= 0 AND cooldown_seconds <= 604800",
            name="ck_auto_drift_config_cooldown",
        ),
        CheckConstraint(
            "resolve_after_clean_windows >= 1 AND resolve_after_clean_windows <= 20",
            name="ck_auto_drift_config_resolution",
        ),
        Index("ix_auto_drift_config_created", "created_at", "id"),
        Index("ix_auto_drift_config_enabled", "is_enabled", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    monitoring_definition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    drift_policy_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("drift_policies.id", ondelete="RESTRICT"), nullable=False
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    baseline_strategy: Mapped[str] = mapped_column(
        String(30), nullable=False, default="previous_window", server_default="previous_window"
    )
    cooldown_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3_600, server_default="3600"
    )
    resolve_after_clean_windows: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AutomaticDriftCheckRecord(Base):
    __tablename__ = "automatic_drift_checks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'completed', 'failed', 'skipped')",
            name="ck_auto_drift_checks_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 4", name="ck_auto_drift_checks_attempts"
        ),
        CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_auto_drift_checks_lease",
        ),
        CheckConstraint(
            "(status IN ('pending', 'queued', 'running') AND completed_at IS NULL "
            "AND drift_comparison_id IS NULL AND skip_reason IS NULL "
            "AND failure_reason IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND drift_comparison_id IS NOT NULL "
            "AND skip_reason IS NULL AND failure_reason IS NULL) OR "
            "(status = 'skipped' AND completed_at IS NOT NULL AND drift_comparison_id IS NULL "
            "AND skip_reason IS NOT NULL AND failure_reason IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND drift_comparison_id IS NULL "
            "AND skip_reason IS NULL AND failure_reason IS NOT NULL)",
            name="ck_auto_drift_checks_lifecycle",
        ),
        CheckConstraint(
            "(event_suppressed AND event_suppression_reason IS NOT NULL) OR "
            "(NOT event_suppressed AND event_suppression_reason IS NULL)",
            name="ck_auto_drift_checks_suppression",
        ),
        Index(
            "uq_auto_drift_checks_identity",
            "automatic_drift_configuration_id",
            "baseline_snapshot_id",
            "current_snapshot_id",
            unique=True,
        ),
        Index(
            "uq_auto_drift_checks_config_current",
            "automatic_drift_configuration_id",
            "current_snapshot_id",
            unique=True,
        ),
        Index(
            "uq_auto_drift_checks_active_config",
            "automatic_drift_configuration_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'queued', 'running')"),
        ),
        Index("ix_auto_drift_checks_recovery", "status", "last_enqueued_at", "lease_expires_at"),
        Index("ix_auto_drift_checks_created", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    automatic_drift_configuration_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("automatic_drift_configurations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    baseline_snapshot_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("monitoring_snapshots.id", ondelete="RESTRICT")
    )
    current_snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    drift_comparison_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("drift_comparisons.id", ondelete="RESTRICT"),
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    skip_reason: Mapped[str | None] = mapped_column(String(50))
    failure_reason: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(String(4_000))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_suppressed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    event_suppression_reason: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MonitoringIncidentRecord(Base):
    __tablename__ = "monitoring_incidents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved')", name="ck_monitoring_incidents_status"
        ),
        CheckConstraint("occurrence_count >= 1", name="ck_monitoring_incidents_occurrences"),
        CheckConstraint("consecutive_clean_count >= 0", name="ck_monitoring_incidents_clean_count"),
        CheckConstraint(
            "(status = 'open' AND acknowledged_at IS NULL AND resolved_at IS NULL "
            "AND resolving_comparison_id IS NULL) OR "
            "(status = 'acknowledged' AND acknowledged_at IS NOT NULL AND resolved_at IS NULL "
            "AND resolving_comparison_id IS NULL) OR "
            "(status = 'resolved' AND resolved_at IS NOT NULL "
            "AND resolving_comparison_id IS NOT NULL)",
            name="ck_monitoring_incidents_lifecycle",
        ),
        Index(
            "uq_monitoring_incidents_active_config",
            "automatic_drift_configuration_id",
            unique=True,
            postgresql_where=text("status IN ('open', 'acknowledged')"),
        ),
        Index("ix_monitoring_incidents_created", "created_at", "id"),
        Index("ix_monitoring_incidents_monitor_status", "monitoring_definition_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    automatic_drift_configuration_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("automatic_drift_configurations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    monitoring_definition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    drift_policy_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("drift_policies.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default="open"
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_drift_comparison_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("drift_comparisons.id", ondelete="RESTRICT"),
        nullable=False,
    )
    latest_drift_comparison_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("drift_comparisons.id", ondelete="RESTRICT"),
        nullable=False,
    )
    resolving_comparison_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("drift_comparisons.id", ondelete="RESTRICT")
    )
    latest_classification: Mapped[str] = mapped_column(String(40), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    consecutive_clean_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_drift_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MonitoringIncidentEventRecord(Base):
    __tablename__ = "monitoring_incident_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('incident_opened', 'drift_reoccurred', "
            "'incident_acknowledged', 'incident_resolved')",
            name="ck_monitoring_incident_events_type",
        ),
        Index("ix_monitoring_incident_events_timeline", "incident_id", "created_at", "id"),
        Index(
            "uq_monitoring_incident_events_check_type",
            "automatic_drift_check_id",
            "event_type",
            unique=True,
            postgresql_where=text("automatic_drift_check_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_incidents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    automatic_drift_check_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("automatic_drift_checks.id", ondelete="RESTRICT")
    )
    drift_comparison_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("drift_comparisons.id", ondelete="RESTRICT")
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
