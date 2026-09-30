from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agentscope_api.schemas.drift import (
    DriftClassification,
    DriftDirection,
    DriftMetric,
    DriftPolicyCreate,
    DriftPolicyRuleCreate,
    DriftThresholdType,
)
from agentscope_api.services.drift import classify_overall, evaluate_rule


def _rule(
    metric: DriftMetric,
    *,
    direction: DriftDirection = DriftDirection.INCREASE,
    threshold_type: DriftThresholdType = DriftThresholdType.ABSOLUTE,
    threshold: float = 0.1,
    minimum_baseline_samples: int = 1,
    minimum_current_samples: int = 1,
) -> DriftPolicyRuleCreate:
    return DriftPolicyRuleCreate(
        metric=metric,
        direction=direction,
        threshold_type=threshold_type,
        practical_threshold=threshold,
        minimum_baseline_samples=minimum_baseline_samples,
        minimum_current_samples=minimum_current_samples,
    )


def _snapshot(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "trace_count": 100,
        "successful_trace_count": 90,
        "failed_trace_count": 10,
        "success_rate": 0.9,
        "failure_rate": 0.1,
        "duration_sample_count": 100,
        "mean_duration_ms": 100.0,
        "p95_duration_ms": 200.0,
        "token_sample_count": 100,
        "mean_total_tokens": 50.0,
        "evaluated_result_count": 100,
        "passed_evaluation_count": 80,
        "failed_evaluation_count": 10,
        "evaluator_error_count": 10,
        "valid_binary_evaluation_count": 90,
        "evaluation_pass_rate": 80 / 90,
        "evaluation_error_rate": 0.1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_policy_requires_one_to_twenty_ordered_rules() -> None:
    rule = _rule(DriftMetric.TRACE_FAILURE_RATE)
    policy = DriftPolicyCreate(name="Production guard", rules=[rule, rule])

    assert [item.metric for item in policy.rules] == [
        DriftMetric.TRACE_FAILURE_RATE,
        DriftMetric.TRACE_FAILURE_RATE,
    ]
    with pytest.raises(ValidationError):
        DriftPolicyCreate(name="empty", rules=[])
    with pytest.raises(ValidationError):
        DriftPolicyCreate(name="too many", rules=[rule] * 21)


@pytest.mark.parametrize(
    "payload",
    [
        {"metric": "unsupported", "direction": "increase", "threshold_type": "absolute"},
        {"metric": "trace_failure_rate", "direction": "sideways", "threshold_type": "absolute"},
        {"metric": "trace_failure_rate", "direction": "increase", "threshold_type": "ratio"},
    ],
)
def test_rule_rejects_unsupported_contract_values(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DriftPolicyRuleCreate(
            **payload,
            practical_threshold=0.1,
            minimum_baseline_samples=1,
            minimum_current_samples=1,
        )


@pytest.mark.parametrize("threshold", [0, -0.1, float("inf"), float("nan")])
def test_rule_rejects_invalid_threshold(threshold: float) -> None:
    with pytest.raises(ValidationError):
        _rule(DriftMetric.TRACE_FAILURE_RATE, threshold=threshold)


@pytest.mark.parametrize("minimum", [0, -1, 1_000_001])
def test_rule_rejects_invalid_minimum_sample(minimum: int) -> None:
    with pytest.raises(ValidationError):
        _rule(DriftMetric.TRACE_FAILURE_RATE, minimum_baseline_samples=minimum)


@pytest.mark.parametrize(
    ("metric", "baseline_overrides", "current_overrides", "direction"),
    [
        (
            DriftMetric.TRACE_FAILURE_RATE,
            {},
            {"failure_rate": 0.2, "failed_trace_count": 20},
            DriftDirection.INCREASE,
        ),
        (
            DriftMetric.TRACE_SUCCESS_RATE,
            {},
            {"success_rate": 0.8, "successful_trace_count": 80},
            DriftDirection.DECREASE,
        ),
        (
            DriftMetric.EVALUATION_PASS_RATE,
            {},
            {"evaluation_pass_rate": 70 / 90, "passed_evaluation_count": 70},
            DriftDirection.DECREASE,
        ),
        (
            DriftMetric.EVALUATION_ERROR_RATE,
            {},
            {"evaluation_error_rate": 0.2, "evaluator_error_count": 20},
            DriftDirection.INCREASE,
        ),
    ],
)
def test_rate_unfavorable_change_at_threshold_detects_drift(
    metric: DriftMetric,
    baseline_overrides: dict[str, object],
    current_overrides: dict[str, object],
    direction: DriftDirection,
) -> None:
    finding = evaluate_rule(
        _rule(metric, direction=direction, threshold=0.1),
        _snapshot(**baseline_overrides),
        _snapshot(**current_overrides),
    )

    assert finding.classification is DriftClassification.DRIFT_DETECTED
    assert abs(finding.absolute_delta or 0) + 1e-12 >= 0.1


def test_favorable_and_subthreshold_changes_do_not_detect_drift() -> None:
    favorable = evaluate_rule(
        _rule(DriftMetric.TRACE_FAILURE_RATE, threshold=0.05),
        _snapshot(),
        _snapshot(failure_rate=0.05, failed_trace_count=5),
    )
    subthreshold = evaluate_rule(
        _rule(DriftMetric.TRACE_FAILURE_RATE, threshold=0.05),
        _snapshot(),
        _snapshot(failure_rate=0.149, failed_trace_count=15),
    )

    assert favorable.classification is DriftClassification.NO_DRIFT_DETECTED
    assert subthreshold.classification is DriftClassification.NO_DRIFT_DETECTED


@pytest.mark.parametrize(
    ("metric", "field", "sample_field"),
    [
        (DriftMetric.MEAN_DURATION_MS, "mean_duration_ms", "duration_sample_count"),
        (DriftMetric.P95_DURATION_MS, "p95_duration_ms", "duration_sample_count"),
        (DriftMetric.MEAN_TOTAL_TOKENS, "mean_total_tokens", "token_sample_count"),
        (DriftMetric.TRACE_COUNT, "trace_count", "trace_count"),
    ],
)
def test_relative_scalar_drift_uses_metric_specific_sample_count(
    metric: DriftMetric, field: str, sample_field: str
) -> None:
    baseline_samples = 100 if metric is DriftMetric.TRACE_COUNT else 12
    current_samples = 125 if metric is DriftMetric.TRACE_COUNT else 13
    baseline = _snapshot(**{field: 100.0, sample_field: baseline_samples})
    current = _snapshot(**{field: 125.0, sample_field: current_samples})
    finding = evaluate_rule(
        _rule(
            metric,
            threshold_type=DriftThresholdType.RELATIVE,
            threshold=0.25,
            minimum_baseline_samples=baseline_samples,
            minimum_current_samples=current_samples,
        ),
        baseline,
        current,
    )

    assert finding.classification is DriftClassification.DRIFT_DETECTED
    assert finding.baseline_sample_count == baseline_samples
    assert finding.current_sample_count == current_samples
    assert finding.relative_delta == pytest.approx(0.25)
    assert finding.z_statistic is finding.p_value is None


def test_relative_zero_baseline_is_insufficient_without_infinity() -> None:
    finding = evaluate_rule(
        _rule(
            DriftMetric.MEAN_DURATION_MS,
            threshold_type=DriftThresholdType.RELATIVE,
        ),
        _snapshot(mean_duration_ms=0),
        _snapshot(mean_duration_ms=10),
    )

    assert finding.classification is DriftClassification.INSUFFICIENT_EVIDENCE
    assert finding.absolute_delta == 10
    assert finding.relative_delta is None


@pytest.mark.parametrize(
    ("rule", "baseline", "current"),
    [
        (
            _rule(DriftMetric.MEAN_DURATION_MS),
            _snapshot(mean_duration_ms=None),
            _snapshot(),
        ),
        (
            _rule(DriftMetric.MEAN_DURATION_MS, minimum_baseline_samples=101),
            _snapshot(),
            _snapshot(),
        ),
        (
            _rule(DriftMetric.MEAN_DURATION_MS, minimum_current_samples=101),
            _snapshot(),
            _snapshot(),
        ),
    ],
)
def test_missing_or_undersized_evidence_is_insufficient(
    rule: DriftPolicyRuleCreate, baseline: SimpleNamespace, current: SimpleNamespace
) -> None:
    assert (
        evaluate_rule(rule, baseline, current).classification
        is DriftClassification.INSUFFICIENT_EVIDENCE
    )


def test_evaluation_error_rate_uses_completed_evaluation_population() -> None:
    finding = evaluate_rule(
        _rule(DriftMetric.EVALUATION_ERROR_RATE),
        _snapshot(evaluated_result_count=40, valid_binary_evaluation_count=10),
        _snapshot(evaluated_result_count=50, valid_binary_evaluation_count=20),
    )

    assert finding.baseline_sample_count == 40
    assert finding.current_sample_count == 50


def test_two_proportion_evidence_is_deterministic_and_descriptive_only() -> None:
    finding = evaluate_rule(
        _rule(DriftMetric.TRACE_FAILURE_RATE, threshold=0.1),
        _snapshot(failure_rate=0.1, failed_trace_count=10),
        _snapshot(failure_rate=0.2, failed_trace_count=20),
    )
    small = evaluate_rule(
        _rule(DriftMetric.TRACE_FAILURE_RATE, threshold=0.4),
        _snapshot(trace_count=2, failure_rate=0.5, failed_trace_count=1),
        _snapshot(trace_count=2, failure_rate=1.0, failed_trace_count=2),
    )

    assert finding.z_statistic == pytest.approx(1.9802950859533488)
    assert finding.p_value == pytest.approx(0.04767038065616144)
    assert small.classification is DriftClassification.DRIFT_DETECTED
    assert small.p_value is not None and small.p_value > 0.05


def test_undefined_rate_statistics_remain_null_and_all_numbers_are_finite() -> None:
    finding = evaluate_rule(
        _rule(DriftMetric.TRACE_SUCCESS_RATE),
        _snapshot(success_rate=1, successful_trace_count=100),
        _snapshot(success_rate=1, successful_trace_count=100),
    )

    assert finding.z_statistic is finding.p_value is None
    assert all(
        value is None or math.isfinite(value)
        for value in (
            finding.baseline_value,
            finding.current_value,
            finding.absolute_delta,
            finding.relative_delta,
            finding.z_statistic,
            finding.p_value,
        )
    )


@pytest.mark.parametrize(
    ("classifications", "expected"),
    [
        (
            [DriftClassification.NO_DRIFT_DETECTED, DriftClassification.DRIFT_DETECTED],
            DriftClassification.DRIFT_DETECTED,
        ),
        (
            [DriftClassification.NO_DRIFT_DETECTED, DriftClassification.NO_DRIFT_DETECTED],
            DriftClassification.NO_DRIFT_DETECTED,
        ),
        (
            [DriftClassification.NO_DRIFT_DETECTED, DriftClassification.INSUFFICIENT_EVIDENCE],
            DriftClassification.INSUFFICIENT_EVIDENCE,
        ),
        (
            [DriftClassification.DRIFT_DETECTED, DriftClassification.INSUFFICIENT_EVIDENCE],
            DriftClassification.DRIFT_DETECTED,
        ),
    ],
)
def test_overall_classification_is_conservative(
    classifications: list[DriftClassification], expected: DriftClassification
) -> None:
    assert classify_overall(classifications) is expected
