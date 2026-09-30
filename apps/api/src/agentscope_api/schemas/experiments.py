from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from agentscope_api.core.json_limits import validate_json_complexity

MAX_EXPERIMENT_SUBJECTS = 1_000
MAX_EVALUATION_CONDITIONS = 20
MAX_PROVENANCE_BYTES = 16 * 1_024
MAX_PAGE_SIZE = 100
MAX_OFFSET = 100_000

Name = Annotated[str, Field(min_length=1, max_length=200)]
Description = Annotated[str, Field(max_length=2_000)]
TraceId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\s]+$")]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class ExperimentStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExperimentCreate(ContractModel):
    name: Name
    description: Description | None = None


class VariantProvenance(ContractModel):
    agent_version: Annotated[str | None, Field(max_length=200)] = None
    model: Annotated[str | None, Field(max_length=200)] = None
    prompt_version: Annotated[str | None, Field(max_length=200)] = None
    workflow_version: Annotated[str | None, Field(max_length=200)] = None
    git_commit_sha: Annotated[str | None, Field(max_length=200)] = None
    deployment_id: Annotated[str | None, Field(max_length=200)] = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        _validate_bounded_json(self.model_dump(mode="json"), "provenance")
        return self


class ExperimentVariantConfigure(ContractModel):
    key: Literal["A", "B"]
    name: Name
    provenance: VariantProvenance = Field(default_factory=VariantProvenance)


class ExperimentSubjectPairConfigure(ContractModel):
    a_trace_id: TraceId
    b_trace_id: TraceId

    @model_validator(mode="after")
    def reject_self_pair(self) -> Self:
        if self.a_trace_id == self.b_trace_id:
            raise ValueError("A and B must reference distinct trace executions")
        return self


class ExperimentConfigure(ContractModel):
    name: Name
    description: Description | None = None
    variants: Annotated[list[ExperimentVariantConfigure], Field(min_length=2, max_length=2)]
    subjects: Annotated[
        list[ExperimentSubjectPairConfigure],
        Field(min_length=1, max_length=MAX_EXPERIMENT_SUBJECTS),
    ]
    evaluation_definition_ids: Annotated[
        list[UUID], Field(min_length=1, max_length=MAX_EVALUATION_CONDITIONS)
    ]

    @model_validator(mode="after")
    def validate_unique_membership(self) -> Self:
        if {variant.key for variant in self.variants} != {"A", "B"}:
            raise ValueError("variants must contain exactly one A and one B")
        trace_ids = [
            trace_id
            for subject in self.subjects
            for trace_id in (subject.a_trace_id, subject.b_trace_id)
        ]
        if len(trace_ids) != len(set(trace_ids)):
            raise ValueError("a trace may appear only once in an experiment population")
        if len(self.evaluation_definition_ids) != len(set(self.evaluation_definition_ids)):
            raise ValueError("evaluation_definition_ids must be unique")
        return self


class ExperimentVariant(ContractModel):
    id: UUID
    key: Literal["A", "B"]
    name: str
    provenance: VariantProvenance


class ExperimentSubjectPair(ContractModel):
    position: int
    a_trace_id: str
    b_trace_id: str


class ExperimentEvaluationCondition(ContractModel):
    position: int
    definition_id: UUID
    definition_name: str
    evaluator_kind: str
    evaluator_config: dict[str, JsonValue]


class Experiment(ContractModel):
    id: UUID
    name: str
    description: str | None
    status: ExperimentStatus
    created_at: datetime
    updated_at: datetime
    ready_at: datetime | None
    variants: list[ExperimentVariant] = Field(default_factory=list)
    subjects: list[ExperimentSubjectPair] = Field(default_factory=list)
    evaluation_conditions: list[ExperimentEvaluationCondition] = Field(default_factory=list)


class ExperimentSummary(ContractModel):
    id: UUID
    name: str
    description: str | None
    status: ExperimentStatus
    created_at: datetime
    updated_at: datetime
    ready_at: datetime | None
    variant_count: int
    subject_count: int
    evaluation_condition_count: int


class ExperimentListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)] = 50
    offset: Annotated[int, Field(ge=0, le=MAX_OFFSET)] = 0
    status: ExperimentStatus | None = None


class ExperimentList(ContractModel):
    items: list[ExperimentSummary]
    has_more: bool


def _validate_bounded_json(value: Any, field_name: str) -> None:
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be valid finite JSON") from error
    if len(encoded) > MAX_PROVENANCE_BYTES:
        raise ValueError(f"{field_name} exceeds {MAX_PROVENANCE_BYTES} bytes")
    validate_json_complexity(value, max_depth=8, max_nodes=500, field_name=field_name)
