from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class DriftMetric(StrEnum):
    TRACE_FAILURE_RATE = "trace_failure_rate"
    TRACE_SUCCESS_RATE = "trace_success_rate"
    EVALUATION_PASS_RATE = "evaluation_pass_rate"
    EVALUATION_ERROR_RATE = "evaluation_error_rate"
    MEAN_DURATION_MS = "mean_duration_ms"
    P95_DURATION_MS = "p95_duration_ms"
    MEAN_TOTAL_TOKENS = "mean_total_tokens"
    TRACE_COUNT = "trace_count"


class DriftDirection(StrEnum):
    INCREASE = "increase"
    DECREASE = "decrease"


class DriftThresholdType(StrEnum):
    ABSOLUTE = "absolute"
    RELATIVE = "relative"


class DriftClassification(StrEnum):
    DRIFT_DETECTED = "drift_detected"
    NO_DRIFT_DETECTED = "no_drift_detected"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class DriftPolicyRuleCreate(ContractModel):
    metric: DriftMetric
    direction: DriftDirection
    threshold_type: DriftThresholdType
    practical_threshold: Annotated[float, Field(gt=0, le=1_000_000_000)]
    minimum_baseline_samples: Annotated[int, Field(ge=1, le=1_000_000)]
    minimum_current_samples: Annotated[int, Field(ge=1, le=1_000_000)]

    @model_validator(mode="after")
    def bound_fractional_thresholds(self) -> DriftPolicyRuleCreate:
        rate_metrics = {
            DriftMetric.TRACE_FAILURE_RATE,
            DriftMetric.TRACE_SUCCESS_RATE,
            DriftMetric.EVALUATION_PASS_RATE,
            DriftMetric.EVALUATION_ERROR_RATE,
        }
        if (
            self.threshold_type is DriftThresholdType.ABSOLUTE
            and self.metric in rate_metrics
            and self.practical_threshold > 1
        ):
            raise ValueError("absolute rate thresholds must not exceed 1")
        if self.threshold_type is DriftThresholdType.RELATIVE and self.practical_threshold > 1_000:
            raise ValueError("relative thresholds must not exceed 1000")
        return self


class DriftPolicyCreate(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str | None, Field(max_length=2_000)] = None
    rules: Annotated[list[DriftPolicyRuleCreate], Field(min_length=1, max_length=20)]


class DriftPolicyRule(DriftPolicyRuleCreate):
    position: int


class DriftPolicy(ContractModel):
    id: UUID
    name: str
    description: str | None
    rules: list[DriftPolicyRule]
    created_at: datetime


class DriftPolicyListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0


class DriftPolicyList(ContractModel):
    items: list[DriftPolicy]
    has_more: bool


class DriftComparisonCreate(ContractModel):
    drift_policy_id: UUID
    baseline_snapshot_id: UUID
    current_snapshot_id: UUID


class DriftFinding(ContractModel):
    rule_position: int
    metric: DriftMetric
    direction: DriftDirection
    threshold_type: DriftThresholdType
    practical_threshold: float
    minimum_baseline_samples: int
    minimum_current_samples: int
    baseline_sample_count: int
    current_sample_count: int
    baseline_value: float | None
    current_value: float | None
    absolute_delta: float | None
    relative_delta: float | None
    classification: DriftClassification
    z_statistic: float | None
    p_value: float | None


class DriftComparisonSummary(ContractModel):
    id: UUID
    monitoring_definition_id: UUID
    drift_policy_id: UUID
    baseline_snapshot_id: UUID
    current_snapshot_id: UUID
    classification: DriftClassification
    policy_name: str
    policy_description: str | None
    created_at: datetime


class DriftComparison(DriftComparisonSummary):
    findings: list[DriftFinding]


class DriftComparisonListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0
    monitoring_definition_id: UUID | None = None
    drift_policy_id: UUID | None = None
    current_snapshot_id: UUID | None = None
    classification: DriftClassification | None = None


class DriftComparisonList(ContractModel):
    items: list[DriftComparisonSummary]
    has_more: bool
