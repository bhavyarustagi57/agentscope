"""Add durable automatic drift checks and incident events."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260925_0018"
down_revision: str | None = "20260925_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "automatic_drift_configurations",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "monitoring_definition_id",
            uuid,
            sa.ForeignKey("monitoring_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "drift_policy_id",
            uuid,
            sa.ForeignKey("drift_policies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "baseline_strategy", sa.String(30), nullable=False, server_default="previous_window"
        ),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("resolve_after_clean_windows", sa.Integer(), nullable=False, server_default="2"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "baseline_strategy = 'previous_window'", name="ck_auto_drift_config_strategy"
        ),
        sa.CheckConstraint(
            "cooldown_seconds >= 0 AND cooldown_seconds <= 604800",
            name="ck_auto_drift_config_cooldown",
        ),
        sa.CheckConstraint(
            "resolve_after_clean_windows >= 1 AND resolve_after_clean_windows <= 20",
            name="ck_auto_drift_config_resolution",
        ),
    )
    op.create_index(
        "ix_auto_drift_config_created", "automatic_drift_configurations", ["created_at", "id"]
    )
    op.create_index(
        "ix_auto_drift_config_enabled", "automatic_drift_configurations", ["is_enabled", "id"]
    )

    op.create_table(
        "automatic_drift_checks",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "automatic_drift_configuration_id",
            uuid,
            sa.ForeignKey("automatic_drift_configurations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "baseline_snapshot_id",
            uuid,
            sa.ForeignKey("monitoring_snapshots.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "current_snapshot_id",
            uuid,
            sa.ForeignKey("monitoring_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "drift_comparison_id",
            uuid,
            sa.ForeignKey("drift_comparisons.id", ondelete="RESTRICT"),
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("skip_reason", sa.String(50)),
        sa.Column("failure_reason", sa.String(50)),
        sa.Column("error_message", sa.String(4_000)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", uuid),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("event_suppressed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("event_suppression_reason", sa.String(50)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'completed', 'failed', 'skipped')",
            name="ck_auto_drift_checks_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 4", name="ck_auto_drift_checks_attempts"
        ),
        sa.CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_auto_drift_checks_lease",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "(event_suppressed AND event_suppression_reason IS NOT NULL) OR "
            "(NOT event_suppressed AND event_suppression_reason IS NULL)",
            name="ck_auto_drift_checks_suppression",
        ),
    )
    op.create_index(
        "uq_auto_drift_checks_identity",
        "automatic_drift_checks",
        ["automatic_drift_configuration_id", "baseline_snapshot_id", "current_snapshot_id"],
        unique=True,
    )
    op.create_index(
        "uq_auto_drift_checks_config_current",
        "automatic_drift_checks",
        ["automatic_drift_configuration_id", "current_snapshot_id"],
        unique=True,
    )
    op.create_index(
        "uq_auto_drift_checks_active_config",
        "automatic_drift_checks",
        ["automatic_drift_configuration_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'queued', 'running')"),
    )
    op.create_index(
        "ix_auto_drift_checks_recovery",
        "automatic_drift_checks",
        ["status", "last_enqueued_at", "lease_expires_at"],
    )
    op.create_index("ix_auto_drift_checks_created", "automatic_drift_checks", ["created_at", "id"])

    op.create_table(
        "monitoring_incidents",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "automatic_drift_configuration_id",
            uuid,
            sa.ForeignKey("automatic_drift_configurations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "monitoring_definition_id",
            uuid,
            sa.ForeignKey("monitoring_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "drift_policy_id",
            uuid,
            sa.ForeignKey("drift_policies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "first_drift_comparison_id",
            uuid,
            sa.ForeignKey("drift_comparisons.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "latest_drift_comparison_id",
            uuid,
            sa.ForeignKey("drift_comparisons.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "resolving_comparison_id",
            uuid,
            sa.ForeignKey("drift_comparisons.id", ondelete="RESTRICT"),
        ),
        sa.Column("latest_classification", sa.String(40), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("consecutive_clean_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_drift_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved')",
            name="ck_monitoring_incidents_status",
        ),
        sa.CheckConstraint("occurrence_count >= 1", name="ck_monitoring_incidents_occurrences"),
        sa.CheckConstraint(
            "consecutive_clean_count >= 0", name="ck_monitoring_incidents_clean_count"
        ),
        sa.CheckConstraint(
            "(status = 'open' AND acknowledged_at IS NULL AND resolved_at IS NULL "
            "AND resolving_comparison_id IS NULL) OR "
            "(status = 'acknowledged' AND acknowledged_at IS NOT NULL AND resolved_at IS NULL "
            "AND resolving_comparison_id IS NULL) OR "
            "(status = 'resolved' AND resolved_at IS NOT NULL "
            "AND resolving_comparison_id IS NOT NULL)",
            name="ck_monitoring_incidents_lifecycle",
        ),
    )
    op.create_index(
        "uq_monitoring_incidents_active_config",
        "monitoring_incidents",
        ["automatic_drift_configuration_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('open', 'acknowledged')"),
    )
    op.create_index("ix_monitoring_incidents_created", "monitoring_incidents", ["created_at", "id"])
    op.create_index(
        "ix_monitoring_incidents_monitor_status",
        "monitoring_incidents",
        ["monitoring_definition_id", "status"],
    )

    op.create_table(
        "monitoring_incident_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "incident_id",
            uuid,
            sa.ForeignKey("monitoring_incidents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "automatic_drift_check_id",
            uuid,
            sa.ForeignKey("automatic_drift_checks.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "drift_comparison_id",
            uuid,
            sa.ForeignKey("drift_comparisons.id", ondelete="RESTRICT"),
        ),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "event_type IN ('incident_opened', 'drift_reoccurred', "
            "'incident_acknowledged', 'incident_resolved')",
            name="ck_monitoring_incident_events_type",
        ),
    )
    op.create_index(
        "ix_monitoring_incident_events_timeline",
        "monitoring_incident_events",
        ["incident_id", "created_at", "id"],
    )
    op.create_index(
        "uq_monitoring_incident_events_check_type",
        "monitoring_incident_events",
        ["automatic_drift_check_id", "event_type"],
        unique=True,
        postgresql_where=sa.text("automatic_drift_check_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_monitoring_incident_events_check_type", table_name="monitoring_incident_events"
    )
    op.drop_index("ix_monitoring_incident_events_timeline", table_name="monitoring_incident_events")
    op.drop_table("monitoring_incident_events")
    op.drop_index("ix_monitoring_incidents_monitor_status", table_name="monitoring_incidents")
    op.drop_index("ix_monitoring_incidents_created", table_name="monitoring_incidents")
    op.drop_index("uq_monitoring_incidents_active_config", table_name="monitoring_incidents")
    op.drop_table("monitoring_incidents")
    op.drop_index("ix_auto_drift_checks_created", table_name="automatic_drift_checks")
    op.drop_index("ix_auto_drift_checks_recovery", table_name="automatic_drift_checks")
    op.drop_index("uq_auto_drift_checks_active_config", table_name="automatic_drift_checks")
    op.drop_index("uq_auto_drift_checks_config_current", table_name="automatic_drift_checks")
    op.drop_index("uq_auto_drift_checks_identity", table_name="automatic_drift_checks")
    op.drop_table("automatic_drift_checks")
    op.drop_index("ix_auto_drift_config_enabled", table_name="automatic_drift_configurations")
    op.drop_index("ix_auto_drift_config_created", table_name="automatic_drift_configurations")
    op.drop_table("automatic_drift_configurations")
