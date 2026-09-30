from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from agentscope_api.schemas.evaluations import (
    EvaluationResultCreate,
    EvaluationRunExecution,
    NumericThresholdConfig,
)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"minimum": 2, "maximum": 1},
        {"minimum": float("nan")},
        {"minimum": float("inf")},
        {"maximum": float("-inf")},
    ],
)
def test_numeric_threshold_rejects_invalid_bounds(config: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        NumericThresholdConfig.model_validate(config)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.01, 1.01])
def test_evaluation_result_rejects_invalid_scores(score: float) -> None:
    with pytest.raises(ValidationError):
        EvaluationResultCreate(
            run_id=uuid4(),
            trace_id="tr_contract",
            outcome="passed",
            score=score,
        )


def test_evaluation_result_rejects_oversized_and_overdeep_details() -> None:
    deep: dict[str, object] = {}
    cursor = deep
    for _ in range(17):
        child: dict[str, object] = {}
        cursor["child"] = child
        cursor = child

    for details in ({"value": "x" * (64 * 1_024)}, deep):
        with pytest.raises(ValidationError):
            EvaluationResultCreate(
                run_id=uuid4(),
                trace_id="tr_contract",
                outcome="passed",
                details=details,
            )


@pytest.mark.parametrize("trace_ids", [[], ["tr_1", "tr_1"], ["contains whitespace"]])
def test_execution_subject_contract_rejects_empty_duplicate_or_invalid_ids(
    trace_ids: list[str],
) -> None:
    with pytest.raises(ValidationError):
        EvaluationRunExecution(trace_ids=trace_ids)
