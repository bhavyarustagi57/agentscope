from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from agentscope_api.core.json_limits import validate_json_complexity

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_OFFSET = 100_000
MAX_CONFIG_BYTES = 32 * 1_024
MAX_DETAILS_BYTES = 64 * 1_024
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 2_000
MAX_TRACES_PER_EXECUTION = 1_000


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class EvaluatorKind(StrEnum):
    EXACT_MATCH = "exact_match"
    CONTAINS = "contains"
    NUMERIC_THRESHOLD = "numeric_threshold"


class EvaluationRunStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EvaluationOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class ExactMatchConfig(ContractModel):
    expected: JsonValue
    case_sensitive: bool = True


class ContainsConfig(ContractModel):
    substring: Annotated[str, Field(min_length=1, max_length=4_000)]
    case_sensitive: bool = True


class NumericThresholdConfig(ContractModel):
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.minimum is None and self.maximum is None:
            raise ValueError("at least one numeric threshold is required")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        return self


_CONFIG_MODELS: dict[EvaluatorKind, type[BaseModel]] = {
    EvaluatorKind.EXACT_MATCH: ExactMatchConfig,
    EvaluatorKind.CONTAINS: ContainsConfig,
    EvaluatorKind.NUMERIC_THRESHOLD: NumericThresholdConfig,
}


class EvaluationDefinitionCreate(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str | None, Field(max_length=2_000)] = None
    evaluator_kind: EvaluatorKind
    evaluator_config: dict[str, JsonValue]
    is_enabled: bool = True

    @model_validator(mode="after")
    def validate_config(self) -> Self:
        config_model = _CONFIG_MODELS[self.evaluator_kind]
        normalized = config_model.model_validate(self.evaluator_config).model_dump(mode="json")
        _validate_bounded_json(normalized, MAX_CONFIG_BYTES, "evaluator_config")
        self.evaluator_config = cast(dict[str, JsonValue], normalized)
        return self


class EvaluationDefinition(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    evaluator_kind: EvaluatorKind
    evaluator_config: dict[str, JsonValue]
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class EvaluationRunCreate(ContractModel):
    definition_id: UUID


class EvaluationRunExecution(ContractModel):
    trace_ids: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\s]+$")]],
        Field(min_length=1, max_length=MAX_TRACES_PER_EXECUTION),
    ]

    @model_validator(mode="after")
    def validate_unique_trace_ids(self) -> Self:
        if len(self.trace_ids) != len(set(self.trace_ids)):
            raise ValueError("trace_ids must be unique")
        return self


class EvaluationRunExecutionAccepted(ContractModel):
    run_id: UUID
    status: Literal["queued"] = "queued"
    subject_count: Annotated[int, Field(ge=1, le=MAX_TRACES_PER_EXECUTION)]
    queue_delivery: Literal["enqueued", "deferred"]


class EvaluationRun(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    definition_id: UUID
    definition_name: str
    evaluator_kind: EvaluatorKind
    evaluator_config: dict[str, JsonValue]
    status: EvaluationRunStatus
    error_message: str | None
    created_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    subject_count: Annotated[int, Field(ge=0, le=MAX_TRACES_PER_EXECUTION)] = 0
    result_count: Annotated[int, Field(ge=0, le=MAX_TRACES_PER_EXECUTION)] = 0
    passed_count: Annotated[int, Field(ge=0, le=MAX_TRACES_PER_EXECUTION)] = 0
    failed_count: Annotated[int, Field(ge=0, le=MAX_TRACES_PER_EXECUTION)] = 0
    error_count: Annotated[int, Field(ge=0, le=MAX_TRACES_PER_EXECUTION)] = 0
    scored_count: Annotated[int, Field(ge=0, le=MAX_TRACES_PER_EXECUTION)] = 0
    average_score: Annotated[float | None, Field(ge=0, le=1)] = None


class EvaluationResultCreate(ContractModel):
    run_id: UUID
    trace_id: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\s]+$")]
    outcome: EvaluationOutcome
    score: Annotated[float | None, Field(ge=0, le=1)] = None
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.outcome is EvaluationOutcome.ERROR and self.score is not None:
            raise ValueError("error outcomes cannot have a score")
        _validate_bounded_json(self.details, MAX_DETAILS_BYTES, "details")
        return self


class EvaluationResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    trace_id: str
    trace_name: str | None = None
    outcome: EvaluationOutcome
    score: float | None
    details: dict[str, JsonValue]
    created_at: datetime


class PageParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE
    offset: Annotated[int, Field(ge=0, le=MAX_OFFSET)] = 0


class DefinitionListParams(PageParams):
    evaluator_kind: EvaluatorKind | None = None
    is_enabled: bool | None = None


class RunListParams(PageParams):
    definition_id: UUID | None = None
    status: EvaluationRunStatus | None = None


class ResultListParams(PageParams):
    trace_id: Annotated[str | None, Field(min_length=1, max_length=128, pattern=r"^[^\s]+$")] = None
    outcome: EvaluationOutcome | None = None


class EvaluationDefinitionList(BaseModel):
    items: list[EvaluationDefinition]
    has_more: bool


class EvaluationRunList(BaseModel):
    items: list[EvaluationRun]
    has_more: bool


class EvaluationResultList(BaseModel):
    items: list[EvaluationResult]
    has_more: bool


def _validate_bounded_json(value: Any, max_bytes: int, field_name: str) -> None:
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be valid finite JSON") from error
    if len(encoded) > max_bytes:
        raise ValueError(f"{field_name} exceeds {max_bytes} bytes")

    validate_json_complexity(
        value,
        max_depth=MAX_JSON_DEPTH,
        max_nodes=MAX_JSON_NODES,
        field_name=field_name,
    )
