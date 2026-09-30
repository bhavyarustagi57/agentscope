from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class ExperimentRunStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExperimentRun(ContractModel):
    id: UUID
    experiment_id: UUID
    status: ExperimentRunStatus
    expected_decision_count: int
    completed_decision_count: int
    evaluator_error_count: int
    remaining_decision_count: int
    error_category: str | None
    error_message: str | None
    created_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    attempt_count: int


class ExperimentRunListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0
    status: ExperimentRunStatus | None = None


class ExperimentRunList(ContractModel):
    items: list[ExperimentRun]
    has_more: bool


class ExperimentRunExecutionAccepted(ContractModel):
    run_id: UUID
    status: Literal["queued"] = "queued"
    queue_delivery: Literal["enqueued", "deferred"]


class ExperimentRunResult(ContractModel):
    run_id: UUID
    experiment_id: UUID
    subject_position: int
    variant: Literal["A", "B"]
    condition_position: int
    trace_id: str
    definition_id: UUID
    definition_name: str
    evaluator_kind: str
    evaluator_config: dict[str, JsonValue]
    outcome: Literal["passed", "failed", "error"]
    score: float | None
    details: dict[str, JsonValue]
    attempt_count: int
    created_at: datetime


class ExperimentRunResultListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0
    variant: Literal["A", "B"] | None = None
    condition_position: Annotated[int | None, Field(ge=0, lt=20)] = None


class ExperimentRunResultList(ContractModel):
    items: list[ExperimentRunResult]
    has_more: bool
