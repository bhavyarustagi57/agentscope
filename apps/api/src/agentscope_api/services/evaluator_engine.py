from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import JsonValue, ValidationError

from agentscope_api.schemas.evaluations import (
    ContainsConfig,
    EvaluationOutcome,
    EvaluatorKind,
    ExactMatchConfig,
    NumericThresholdConfig,
)


class UnsupportedEvaluatorKind(ValueError):
    pass


class InvalidEvaluatorConfiguration(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EvaluationInput:
    trace_id: str
    candidate: Any
    captured: bool


@dataclass(frozen=True, slots=True)
class EvaluationDecision:
    outcome: EvaluationOutcome
    score: float | None
    details: dict[str, JsonValue]


class Evaluator(Protocol):
    kind: EvaluatorKind

    def evaluate(self, input_: EvaluationInput) -> EvaluationDecision: ...


@dataclass(frozen=True, slots=True)
class ExactMatchEvaluator:
    config: ExactMatchConfig
    kind = EvaluatorKind.EXACT_MATCH

    def evaluate(self, input_: EvaluationInput) -> EvaluationDecision:
        error = _input_error(input_)
        if error is not None:
            return error
        candidate = input_.candidate
        if not _is_json_value(candidate):
            return _error("unsupported_candidate_type", candidate)

        expected = self.config.expected
        if isinstance(candidate, str) and isinstance(expected, str):
            matched = (
                candidate == expected
                if self.config.case_sensitive
                else candidate.casefold() == expected.casefold()
            )
        else:
            matched = _canonical_json(candidate) == _canonical_json(expected)
        return _binary_decision(
            matched,
            "exact_match" if matched else "value_mismatch",
            {
                "comparison": "exact",
                "case_sensitive": self.config.case_sensitive,
                "candidate_type": _json_type(candidate),
                "expected_type": _json_type(expected),
            },
        )


@dataclass(frozen=True, slots=True)
class ContainsEvaluator:
    config: ContainsConfig
    kind = EvaluatorKind.CONTAINS

    def evaluate(self, input_: EvaluationInput) -> EvaluationDecision:
        error = _input_error(input_)
        if error is not None:
            return error
        candidate = input_.candidate
        if not isinstance(candidate, str):
            return _error("unsupported_candidate_type", candidate)
        actual = candidate if self.config.case_sensitive else candidate.casefold()
        expected = (
            self.config.substring
            if self.config.case_sensitive
            else self.config.substring.casefold()
        )
        matched = expected in actual
        return _binary_decision(
            matched,
            "contains_match" if matched else "substring_not_found",
            {"comparison": "literal_contains", "case_sensitive": self.config.case_sensitive},
        )


@dataclass(frozen=True, slots=True)
class NumericThresholdEvaluator:
    config: NumericThresholdConfig
    kind = EvaluatorKind.NUMERIC_THRESHOLD

    def evaluate(self, input_: EvaluationInput) -> EvaluationDecision:
        error = _input_error(input_)
        if error is not None:
            return error
        candidate = input_.candidate
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
            return _error("unsupported_candidate_type", candidate)
        if not math.isfinite(candidate):
            return _error("non_finite_candidate", candidate)
        if self.config.minimum is not None and candidate < self.config.minimum:
            return _binary_decision(
                False,
                "below_minimum",
                {"comparison": "inclusive_range", "minimum_applied": True},
            )
        if self.config.maximum is not None and candidate > self.config.maximum:
            return _binary_decision(
                False,
                "above_maximum",
                {"comparison": "inclusive_range", "maximum_applied": True},
            )
        return _binary_decision(
            True,
            "within_threshold",
            {
                "comparison": "inclusive_range",
                "minimum_applied": self.config.minimum is not None,
                "maximum_applied": self.config.maximum is not None,
            },
        )


def resolve_evaluator(kind: str, config: dict[str, object]) -> Evaluator:
    try:
        evaluator_kind = EvaluatorKind(kind)
    except ValueError as error:
        raise UnsupportedEvaluatorKind("unsupported evaluator kind") from error
    try:
        if evaluator_kind is EvaluatorKind.EXACT_MATCH:
            return ExactMatchEvaluator(ExactMatchConfig.model_validate(config))
        if evaluator_kind is EvaluatorKind.CONTAINS:
            return ContainsEvaluator(ContainsConfig.model_validate(config))
        return NumericThresholdEvaluator(NumericThresholdConfig.model_validate(config))
    except ValidationError as error:
        raise InvalidEvaluatorConfiguration("invalid evaluator configuration") from error


def _input_error(input_: EvaluationInput) -> EvaluationDecision | None:
    if not input_.captured:
        return EvaluationDecision(
            outcome=EvaluationOutcome.ERROR,
            score=None,
            details={"reason_code": "candidate_not_captured"},
        )
    return None


def _error(reason: str, candidate: object) -> EvaluationDecision:
    return EvaluationDecision(
        outcome=EvaluationOutcome.ERROR,
        score=None,
        details={"reason_code": reason, "candidate_type": _json_type(candidate)},
    )


def _binary_decision(
    matched: bool, reason: str, details: dict[str, JsonValue]
) -> EvaluationDecision:
    return EvaluationDecision(
        outcome=EvaluationOutcome.PASSED if matched else EvaluationOutcome.FAILED,
        score=1.0 if matched else 0.0,
        details={"reason_code": reason, **details},
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _is_json_value(value: object) -> bool:
    stack = [value]
    while stack:
        item = stack.pop()
        if item is None or isinstance(item, (str, bool, int)):
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                return False
            continue
        if isinstance(item, list):
            stack.extend(item)
            continue
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            stack.extend(item.values())
            continue
        return False
    return True


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unsupported"
