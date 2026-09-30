from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Name = Annotated[str, Field(min_length=1, max_length=200)]
ModelId = Annotated[
    str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
]
Rubric = Annotated[str, Field(min_length=1, max_length=8_000)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class JudgeDecision(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class JudgeRunStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JudgeErrorCategory(StrEnum):
    CANDIDATE_UNAVAILABLE = "candidate_unavailable"
    CANDIDATE_TOO_LARGE = "candidate_too_large"
    INVALID_PROVIDER_RESPONSE = "invalid_provider_response"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_AUTHENTICATION = "provider_authentication"
    INVALID_CONFIGURATION = "invalid_configuration"
    INTERNAL = "internal"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"


class JudgeConfigurationCreate(ContractModel):
    name: Name
    description: Annotated[str, Field(max_length=2_000)] | None = None
    provider: Literal["openai"] = "openai"
    model: ModelId
    rubric: Rubric
    output_schema_version: Literal["1"] = "1"
    timeout_seconds: Annotated[int, Field(ge=5, le=300)] = 30
    max_output_tokens: Annotated[int, Field(ge=32, le=1_000)] = 300
    configuration_version: Literal["1"] = "1"


class JudgeConfiguration(JudgeConfigurationCreate):
    id: UUID
    created_at: datetime


class JudgeConfigurationList(ContractModel):
    items: list[JudgeConfiguration]
    has_more: bool


class JudgeRunCreate(ContractModel):
    study_id: UUID
    configuration_id: UUID


class JudgeRun(ContractModel):
    id: UUID
    study_id: UUID
    configuration_id: UUID
    provider: str
    model: str
    rubric: str
    output_schema_version: str
    timeout_seconds: int
    max_output_tokens: int
    configuration_version: str
    status: JudgeRunStatus
    error_category: JudgeErrorCategory | None
    subject_count: int
    result_count: int
    created_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    attempt_count: int


class JudgeRunList(ContractModel):
    items: list[JudgeRun]
    has_more: bool


class JudgeRunSubmission(ContractModel):
    run_id: UUID
    status: JudgeRunStatus


class JudgeResult(ContractModel):
    run_id: UUID
    study_id: UUID
    trace_id: str
    decision: JudgeDecision | None
    rationale: str | None
    error_category: JudgeErrorCategory | None
    provider_request_id: str | None
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    latency_ms: float | None
    attempt_count: int
    created_at: datetime


class JudgeResultList(ContractModel):
    items: list[JudgeResult]
    has_more: bool


class JudgeRunProgress(ContractModel):
    run_id: UUID
    status: JudgeRunStatus
    subject_count: int
    result_count: int
    passed_count: int
    failed_count: int
    error_count: int
    pending_count: int
