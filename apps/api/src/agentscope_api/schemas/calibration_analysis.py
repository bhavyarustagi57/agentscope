from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agentscope_api.schemas.calibration import PageParams


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class UndefinedMetric(StrEnum):
    PRECISION_PASSED = "precision_passed"
    RECALL_PASSED = "recall_passed"
    F1_PASSED = "f1_passed"
    SPECIFICITY_FAILED = "specificity_failed"
    COHENS_KAPPA = "cohens_kappa"


class DisagreementCategory(StrEnum):
    FALSE_POSITIVE = "false_positive"
    FALSE_NEGATIVE = "false_negative"


class CalibrationMetrics(ContractModel):
    sample_count: int
    human_passed_count: int
    human_failed_count: int
    judge_passed_count: int
    judge_failed_count: int
    agreement_count: int
    disagreement_count: int
    true_positive: int
    true_negative: int
    false_positive: int
    false_negative: int
    observed_agreement: float
    expected_agreement: float
    precision_passed: float | None
    recall_passed: float | None
    f1_passed: float | None
    specificity_failed: float | None
    cohens_kappa: float | None
    kappa_is_defined: bool
    undefined_metrics: list[UndefinedMetric]


class CalibrationAnalysis(CalibrationMetrics):
    run_id: UUID
    study_id: UUID
    metric_schema_version: str
    created_at: datetime


class CalibrationDisagreement(ContractModel):
    trace_id: str
    category: DisagreementCategory
    human_label: str
    judge_decision: str


class CalibrationDisagreementList(ContractModel):
    items: list[CalibrationDisagreement]
    has_more: bool


class DisagreementListParams(PageParams):
    category: Annotated[str, Field(max_length=32)] | None = None
