from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentscope_api.schemas.traces import TraceStatus


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class WindowDuration(StrEnum):
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    ONE_HOUR = "1h"
    SIX_HOURS = "6h"
    ONE_DAY = "24h"

    @property
    def seconds(self) -> int:
        return {
            self.FIVE_MINUTES: 300,
            self.FIFTEEN_MINUTES: 900,
            self.ONE_HOUR: 3_600,
            self.SIX_HOURS: 21_600,
            self.ONE_DAY: 86_400,
        }[self]

    @classmethod
    def from_seconds(cls, seconds: int) -> WindowDuration:
        for value in cls:
            if value.seconds == seconds:
                return value
        raise ValueError("unsupported window duration")


class MonitoringTraceScope(ContractModel):
    trace_name: Annotated[str | None, Field(min_length=1, max_length=500)] = None
    trace_status: TraceStatus | None = None


class MonitoringDefinitionCreate(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str | None, Field(max_length=2_000)] = None
    is_enabled: bool = True
    window_duration: WindowDuration
    trace_scope: MonitoringTraceScope = Field(default_factory=MonitoringTraceScope)
    evaluation_definition_id: UUID | None = None


class MonitoringDefinitionEnabledUpdate(ContractModel):
    is_enabled: bool


class MonitoringDefinition(ContractModel):
    id: UUID
    name: str
    description: str | None
    is_enabled: bool
    window_duration: WindowDuration
    trace_scope: MonitoringTraceScope
    evaluation_definition_id: UUID | None
    created_at: datetime
    updated_at: datetime


class MonitoringDefinitionListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0
    is_enabled: bool | None = None


class MonitoringDefinitionList(ContractModel):
    items: list[MonitoringDefinition]
    has_more: bool


class MonitoringSnapshotStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MonitoringMaterializationRequest(ContractModel):
    window_start: datetime | None = None
    window_end: datetime | None = None

    @model_validator(mode="after")
    def require_both_or_neither(self) -> MonitoringMaterializationRequest:
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("window_start and window_end must be provided together")
        for value in (self.window_start, self.window_end):
            if value is not None and value.utcoffset() is None:
                raise ValueError("window timestamps must include a UTC offset")
        return self


class MonitoringSnapshot(ContractModel):
    id: UUID
    monitoring_definition_id: UUID
    status: MonitoringSnapshotStatus
    window_start: datetime
    window_end: datetime
    trace_count: int
    successful_trace_count: int
    failed_trace_count: int
    success_rate: float | None
    failure_rate: float | None
    duration_sample_count: int
    mean_duration_ms: float | None
    median_duration_ms: float | None
    p95_duration_ms: float | None
    token_sample_count: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    mean_total_tokens: float | None
    evaluated_result_count: int
    passed_evaluation_count: int
    failed_evaluation_count: int
    evaluator_error_count: int
    valid_binary_evaluation_count: int
    evaluation_pass_rate: float | None
    evaluation_error_rate: float | None
    attempt_count: int
    error_message: str | None
    created_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None


class MonitoringSnapshotAccepted(ContractModel):
    snapshot_id: UUID
    status: MonitoringSnapshotStatus
    queue_delivery: Literal["enqueued", "deferred", "not_required"]
    created: bool


class MonitoringSnapshotListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0
    status: MonitoringSnapshotStatus | None = None
    window_start_gte: datetime | None = None
    window_end_lte: datetime | None = None

    @model_validator(mode="after")
    def validate_time_filters(self) -> MonitoringSnapshotListParams:
        for value in (self.window_start_gte, self.window_end_lte):
            if value is not None and value.utcoffset() is None:
                raise ValueError("window timestamps must include a UTC offset")
        if (
            self.window_start_gte is not None
            and self.window_end_lte is not None
            and self.window_start_gte > self.window_end_lte
        ):
            raise ValueError("window_start_gte must not exceed window_end_lte")
        return self


class MonitoringSnapshotList(ContractModel):
    items: list[MonitoringSnapshot]
    has_more: bool
