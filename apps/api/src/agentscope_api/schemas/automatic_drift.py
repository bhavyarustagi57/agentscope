from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class BaselineStrategy(StrEnum):
    PREVIOUS_WINDOW = "previous_window"


class AutomaticDriftCheckStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class MonitoringIncidentStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class MonitoringIncidentEventType(StrEnum):
    INCIDENT_OPENED = "incident_opened"
    DRIFT_REOCCURRED = "drift_reoccurred"
    INCIDENT_ACKNOWLEDGED = "incident_acknowledged"
    INCIDENT_RESOLVED = "incident_resolved"


class AutomaticDriftConfigurationCreate(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    monitoring_definition_id: UUID
    drift_policy_id: UUID
    is_enabled: bool = True
    baseline_strategy: BaselineStrategy = BaselineStrategy.PREVIOUS_WINDOW
    cooldown_seconds: Annotated[int, Field(ge=0, le=604_800)] = 3_600
    resolve_after_clean_windows: Annotated[int, Field(ge=1, le=20)] = 2


class AutomaticDriftConfigurationUpdate(ContractModel):
    is_enabled: bool


class AutomaticDriftConfiguration(AutomaticDriftConfigurationCreate):
    id: UUID
    created_at: datetime
    updated_at: datetime


class AutomaticDriftConfigurationListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0
    is_enabled: bool | None = None
    monitoring_definition_id: UUID | None = None
    drift_policy_id: UUID | None = None


class AutomaticDriftConfigurationList(ContractModel):
    items: list[AutomaticDriftConfiguration]
    has_more: bool


class AutomaticDriftCheck(ContractModel):
    id: UUID
    automatic_drift_configuration_id: UUID
    baseline_snapshot_id: UUID | None
    current_snapshot_id: UUID
    drift_comparison_id: UUID | None
    status: AutomaticDriftCheckStatus
    skip_reason: str | None
    failure_reason: str | None
    error_message: str | None
    attempt_count: int
    event_suppressed: bool
    event_suppression_reason: str | None
    created_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None


class AutomaticDriftCheckListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0
    automatic_drift_configuration_id: UUID | None = None
    current_snapshot_id: UUID | None = None
    status: AutomaticDriftCheckStatus | None = None


class AutomaticDriftCheckList(ContractModel):
    items: list[AutomaticDriftCheck]
    has_more: bool


class MonitoringIncident(ContractModel):
    id: UUID
    automatic_drift_configuration_id: UUID
    monitoring_definition_id: UUID
    drift_policy_id: UUID
    status: MonitoringIncidentStatus
    opened_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    first_drift_comparison_id: UUID
    latest_drift_comparison_id: UUID
    resolving_comparison_id: UUID | None
    latest_classification: str
    occurrence_count: int
    consecutive_clean_count: int
    created_at: datetime
    updated_at: datetime


class MonitoringIncidentListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0
    monitoring_definition_id: UUID | None = None
    automatic_drift_configuration_id: UUID | None = None
    status: MonitoringIncidentStatus | None = None


class MonitoringIncidentList(ContractModel):
    items: list[MonitoringIncident]
    has_more: bool


class MonitoringIncidentEvent(ContractModel):
    id: UUID
    incident_id: UUID
    automatic_drift_check_id: UUID | None
    drift_comparison_id: UUID | None
    event_type: MonitoringIncidentEventType
    created_at: datetime


class MonitoringIncidentEventListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0


class MonitoringIncidentEventList(ContractModel):
    items: list[MonitoringIncidentEvent]
    has_more: bool
