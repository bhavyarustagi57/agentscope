from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class PairedBinaryStatistics(ContractModel):
    sample_size: int
    a_passed_count: int
    a_failed_count: int
    b_passed_count: int
    b_failed_count: int
    a_pass_rate: float
    b_pass_rate: float
    pass_rate_difference: float
    both_passed_count: int
    both_passed_rate: float
    both_failed_count: int
    both_failed_rate: float
    a_only_passed_count: int
    a_only_passed_rate: float
    b_only_passed_count: int
    b_only_passed_rate: float
    matched_pairs_odds_ratio: float | None
    confidence_level: float
    confidence_interval_lower: float
    confidence_interval_upper: float
    confidence_method: str
    test_method: str
    discordant_count: int
    test_statistic: float | None
    p_value: float
    alpha: float
    rejects_null: bool


class EligibleConditionResult(PairedBinaryStatistics):
    eligible: Literal[True] = True
    ineligible_reason: None = None


class IneligibleConditionResult(ContractModel):
    eligible: Literal[False] = False
    ineligible_reason: Literal["non_binary_outcome"]


class ExperimentConditionAnalysis(ContractModel):
    condition_position: int
    definition_id: UUID
    definition_name: str
    evaluator_kind: str
    result: Annotated[
        EligibleConditionResult | IneligibleConditionResult,
        Field(discriminator="eligible"),
    ]


class ExperimentRunAnalysis(ContractModel):
    id: UUID
    run_id: UUID
    experiment_id: UUID
    analysis_schema_version: str
    confidence_method: str
    confidence_level: float
    bootstrap_seed: int
    bootstrap_iterations: int
    hypothesis_test_method: str
    alpha: float
    created_at: datetime
    conditions: list[ExperimentConditionAnalysis]


class ChangeDirection(StrEnum):
    A_PASS_B_FAIL = "A_PASS_B_FAIL"
    A_FAIL_B_PASS = "A_FAIL_B_PASS"


class ExperimentChangeListParams(ContractModel):
    condition_position: Annotated[int, Field(ge=0, lt=20)]
    direction: ChangeDirection | None = None
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0


class ExperimentChangedSubject(ContractModel):
    subject_position: int
    condition_position: int
    definition_id: UUID
    definition_name: str
    direction: ChangeDirection
    a_trace_id: str
    b_trace_id: str
    a_outcome: Literal["passed", "failed"]
    b_outcome: Literal["passed", "failed"]


class ExperimentChangedSubjectList(ContractModel):
    items: list[ExperimentChangedSubject]
    has_more: bool
