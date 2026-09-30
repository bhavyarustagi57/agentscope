from __future__ import annotations

from typing import Any

import pytest

from agentscope_api.schemas.evaluations import EvaluationOutcome
from agentscope_api.services.evaluator_engine import (
    EvaluationInput,
    InvalidEvaluatorConfiguration,
    UnsupportedEvaluatorKind,
    resolve_evaluator,
)


def evaluate(
    kind: str,
    config: dict[str, object],
    candidate: Any,
    *,
    captured: bool = True,
):
    evaluator = resolve_evaluator(kind, config)
    return evaluator.evaluate(
        EvaluationInput(trace_id="trace-contract-test", candidate=candidate, captured=captured)
    )


@pytest.mark.parametrize(
    ("candidate", "expected", "case_sensitive", "outcome"),
    [
        ("Paris", "Paris", True, EvaluationOutcome.PASSED),
        ("paris", "Paris", True, EvaluationOutcome.FAILED),
        ("Straße", "STRASSE", False, EvaluationOutcome.PASSED),
        (" Paris ", "Paris", False, EvaluationOutcome.FAILED),
        ({"answer": [1, True]}, {"answer": [1, True]}, True, EvaluationOutcome.PASSED),
        ({"answer": [True]}, {"answer": [1]}, True, EvaluationOutcome.FAILED),
        (["é", {"value": -0.0}], ["é", {"value": -0.0}], True, EvaluationOutcome.PASSED),
        (None, None, True, EvaluationOutcome.PASSED),
        (1, 1.0, True, EvaluationOutcome.FAILED),
    ],
)
def test_exact_match_has_explicit_string_and_structured_semantics(
    candidate: object,
    expected: object,
    case_sensitive: bool,
    outcome: EvaluationOutcome,
) -> None:
    decision = evaluate(
        "exact_match",
        {"expected": expected, "case_sensitive": case_sensitive},
        candidate,
    )

    assert decision.outcome is outcome
    assert decision.score == (1.0 if outcome is EvaluationOutcome.PASSED else 0.0)
    assert decision.details["reason_code"] in {"exact_match", "value_mismatch"}
    assert "candidate" not in decision.details
    assert "expected" not in decision.details


@pytest.mark.parametrize(
    ("candidate", "captured", "reason"),
    [
        (None, False, "candidate_not_captured"),
        (object(), True, "unsupported_candidate_type"),
    ],
)
def test_exact_match_reports_input_errors_without_turning_them_into_failures(
    candidate: object, captured: bool, reason: str
) -> None:
    decision = evaluate(
        "exact_match",
        {"expected": "Paris", "case_sensitive": True},
        candidate,
        captured=captured,
    )

    assert decision.outcome is EvaluationOutcome.ERROR
    assert decision.score is None
    assert decision.details["reason_code"] == reason


@pytest.mark.parametrize(
    ("candidate", "substring", "case_sensitive", "outcome"),
    [
        ("request approved", "approved", True, EvaluationOutcome.PASSED),
        ("request denied", "approved", True, EvaluationOutcome.FAILED),
        ("CAFÉ READY", "café", False, EvaluationOutcome.PASSED),
        ("Straße", "STRASSE", False, EvaluationOutcome.PASSED),
    ],
)
def test_contains_is_literal_unicode_aware_and_string_only(
    candidate: object,
    substring: str,
    case_sensitive: bool,
    outcome: EvaluationOutcome,
) -> None:
    decision = evaluate(
        "contains",
        {"substring": substring, "case_sensitive": case_sensitive},
        candidate,
    )

    assert decision.outcome is outcome
    assert decision.score == (1.0 if outcome is EvaluationOutcome.PASSED else 0.0)


@pytest.mark.parametrize(
    ("candidate", "captured", "reason"),
    [
        (None, False, "candidate_not_captured"),
        ({"text": "approved"}, True, "unsupported_candidate_type"),
    ],
)
def test_contains_reports_missing_or_non_string_candidates(
    candidate: object, captured: bool, reason: str
) -> None:
    decision = evaluate(
        "contains",
        {"substring": "approved", "case_sensitive": True},
        candidate,
        captured=captured,
    )

    assert decision.outcome is EvaluationOutcome.ERROR
    assert decision.score is None
    assert decision.details["reason_code"] == reason


@pytest.mark.parametrize(
    ("candidate", "config", "outcome", "reason"),
    [
        (3, {"minimum": 3}, EvaluationOutcome.PASSED, "within_threshold"),
        (2.99, {"minimum": 3}, EvaluationOutcome.FAILED, "below_minimum"),
        (0, {"maximum": 0}, EvaluationOutcome.PASSED, "within_threshold"),
        (0.01, {"maximum": 0}, EvaluationOutcome.FAILED, "above_maximum"),
        (-1.5, {"minimum": -2, "maximum": -1}, EvaluationOutcome.PASSED, "within_threshold"),
        (-2.01, {"minimum": -2, "maximum": -1}, EvaluationOutcome.FAILED, "below_minimum"),
        (-0.0, {"minimum": 0, "maximum": 0}, EvaluationOutcome.PASSED, "within_threshold"),
        (1e308, {"minimum": 1e307}, EvaluationOutcome.PASSED, "within_threshold"),
    ],
)
def test_numeric_threshold_uses_inclusive_finite_bounds(
    candidate: object,
    config: dict[str, object],
    outcome: EvaluationOutcome,
    reason: str,
) -> None:
    decision = evaluate("numeric_threshold", config, candidate)

    assert decision.outcome is outcome
    assert decision.score == (1.0 if outcome is EvaluationOutcome.PASSED else 0.0)
    assert decision.details["reason_code"] == reason


@pytest.mark.parametrize(
    ("candidate", "captured", "reason"),
    [
        (None, False, "candidate_not_captured"),
        (True, True, "unsupported_candidate_type"),
        ("3", True, "unsupported_candidate_type"),
        (float("nan"), True, "non_finite_candidate"),
        (float("inf"), True, "non_finite_candidate"),
        (float("-inf"), True, "non_finite_candidate"),
    ],
)
def test_numeric_threshold_rejects_unavailable_or_non_numeric_candidates(
    candidate: object, captured: bool, reason: str
) -> None:
    decision = evaluate("numeric_threshold", {"minimum": 0}, candidate, captured=captured)

    assert decision.outcome is EvaluationOutcome.ERROR
    assert decision.score is None
    assert decision.details["reason_code"] == reason


@pytest.mark.parametrize("kind", ["exact_match", "contains", "numeric_threshold"])
def test_registry_resolves_every_supported_kind(kind: str) -> None:
    configs = {
        "exact_match": {"expected": "ok"},
        "contains": {"substring": "ok"},
        "numeric_threshold": {"minimum": 0},
    }

    assert resolve_evaluator(kind, configs[kind]).kind.value == kind


def test_registry_rejects_unknown_kinds_and_kind_specific_invalid_config() -> None:
    with pytest.raises(UnsupportedEvaluatorKind):
        resolve_evaluator("not.importable.module", {})
    with pytest.raises(InvalidEvaluatorConfiguration):
        resolve_evaluator("contains", {"substring": "", "minimum": 1})
