from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class RegressionPolicyCreate(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=2_000)] | None = None
    minimum_pass_rate_drop: Annotated[float, Field(gt=0, le=1)]
    minimum_sample_size: Annotated[int, Field(ge=1, le=1_000)]


class RegressionPolicy(RegressionPolicyCreate):
    id: UUID
    created_at: datetime


class RegressionPolicyListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0


class RegressionPolicyList(ContractModel):
    items: list[RegressionPolicy]
    has_more: bool


class RegressionClassification(StrEnum):
    REGRESSION_DETECTED = "regression_detected"
    NO_REGRESSION_DETECTED = "no_regression_detected"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class RegressionCheckCreate(ContractModel):
    experiment_run_id: UUID
    regression_policy_id: UUID
    baseline_variant: Literal["A", "B"]
    candidate_variant: Literal["A", "B"]

    @model_validator(mode="after")
    def variants_are_distinct(self) -> RegressionCheckCreate:
        if self.baseline_variant == self.candidate_variant:
            raise ValueError("baseline_variant and candidate_variant must differ")
        return self


class RegressionFinding(ContractModel):
    condition_position: int
    analysis_id: UUID
    definition_id: UUID
    definition_name: str
    evaluator_kind: str
    evaluator_config: dict[str, object]
    baseline_variant: Literal["A", "B"]
    candidate_variant: Literal["A", "B"]
    source_eligible: bool
    source_ineligible_reason: str | None
    classification: RegressionClassification
    minimum_pass_rate_drop: float
    minimum_sample_size: int
    sample_size: int | None
    baseline_passed_count: int | None
    candidate_passed_count: int | None
    baseline_pass_rate: float | None
    candidate_pass_rate: float | None
    candidate_minus_baseline: float | None
    both_passed_count: int | None
    both_failed_count: int | None
    baseline_only_passed_count: int | None
    candidate_only_passed_count: int | None
    discordant_count: int | None
    matched_pairs_odds_ratio: float | None
    p_value: float | None
    rejects_null: bool | None
    source_interval_lower: float | None
    source_interval_upper: float | None
    confidence_interval_lower: float | None
    confidence_interval_upper: float | None
    created_at: datetime


class RegressionCheckSummary(ContractModel):
    id: UUID
    experiment_run_id: UUID
    experiment_id: UUID
    analysis_id: UUID
    regression_policy_id: UUID
    baseline_variant: Literal["A", "B"]
    candidate_variant: Literal["A", "B"]
    classification: RegressionClassification
    policy_name: str
    policy_description: str | None
    minimum_pass_rate_drop: float
    minimum_sample_size: int
    baseline_provenance: dict[str, object]
    candidate_provenance: dict[str, object]
    created_at: datetime


class RegressionCheck(RegressionCheckSummary):
    findings: list[RegressionFinding]


class RegressionCheckListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0
    experiment_run_id: UUID | None = None


class RegressionCheckList(ContractModel):
    items: list[RegressionCheckSummary]
    has_more: bool
