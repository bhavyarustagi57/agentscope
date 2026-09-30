from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentscope_api.schemas.calibration import (
    HumanAnnotationWrite,
    ReferenceLabelWrite,
    ReferenceSubjectsDefine,
)


@pytest.mark.parametrize("annotator_id", ["", "contains whitespace", "x" * 101, "bad/slash"])
def test_annotator_identifier_is_bounded_and_url_safe(annotator_id: str) -> None:
    with pytest.raises(ValidationError):
        HumanAnnotationWrite(trace_id="tr_1", annotator_id=annotator_id, label="passed")


@pytest.mark.parametrize("label", ["error", "missing", "skipped", "unlabeled"])
def test_human_labels_reject_operational_or_missing_states(label: str) -> None:
    with pytest.raises(ValidationError):
        ReferenceLabelWrite.model_validate({"label": label, "annotator_id": "reviewer-1"})


def test_subject_and_rationale_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        ReferenceSubjectsDefine(trace_ids=[])
    with pytest.raises(ValidationError):
        ReferenceSubjectsDefine(trace_ids=["tr_1", "tr_1"])
    with pytest.raises(ValidationError):
        ReferenceSubjectsDefine(trace_ids=[f"tr_{index}" for index in range(501)])
    with pytest.raises(ValidationError):
        HumanAnnotationWrite(
            trace_id="tr_1", annotator_id="reviewer-1", label="failed", rationale="x" * 4_001
        )
