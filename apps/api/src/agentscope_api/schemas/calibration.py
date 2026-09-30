from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_REFERENCE_SUBJECTS = 500
MAX_PAGE_SIZE = 100
MAX_OFFSET = 100_000

Name = Annotated[str, Field(min_length=1, max_length=200)]
Description = Annotated[str, Field(max_length=2_000)]
TraceId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\s]+$")]
AnnotatorId = Annotated[
    str,
    Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]
Rationale = Annotated[str, Field(max_length=4_000)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class ReferenceSetStatus(StrEnum):
    DRAFT = "draft"
    LABELING = "labeling"
    FROZEN = "frozen"


class HumanLabel(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class CalibrationStudyStatus(StrEnum):
    DRAFT = "draft"


class PageParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)] = 50
    offset: Annotated[int, Field(ge=0, le=MAX_OFFSET)] = 0


class HumanReferenceSetCreate(ContractModel):
    name: Name
    description: Description | None = None


class HumanReferenceSet(ContractModel):
    id: UUID
    name: str
    description: str | None
    status: ReferenceSetStatus
    created_at: datetime
    updated_at: datetime
    frozen_at: datetime | None
    subject_count: int = 0
    annotation_count: int = 0
    reference_count: int = 0
    unlabeled_count: int = 0
    passed_count: int = 0
    failed_count: int = 0


class HumanReferenceSetList(ContractModel):
    items: list[HumanReferenceSet]
    has_more: bool


class ReferenceSubjectsDefine(ContractModel):
    trace_ids: Annotated[list[TraceId], Field(min_length=1, max_length=MAX_REFERENCE_SUBJECTS)]

    @model_validator(mode="after")
    def reject_duplicates(self) -> ReferenceSubjectsDefine:
        if len(set(self.trace_ids)) != len(self.trace_ids):
            raise ValueError("trace_ids must be unique")
        return self


class HumanReferenceSubject(ContractModel):
    reference_set_id: UUID
    trace_id: str
    position: int
    trace_name: str
    reference_label: HumanLabel | None
    reference_rationale: str | None
    reference_annotator_id: str | None
    reference_labeled_at: datetime | None
    annotation_count: int
    created_at: datetime


class HumanReferenceSubjectList(ContractModel):
    items: list[HumanReferenceSubject]
    has_more: bool


class HumanAnnotationWrite(ContractModel):
    trace_id: TraceId
    annotator_id: AnnotatorId
    label: HumanLabel
    rationale: Rationale | None = None
    source: Annotated[str, Field(pattern="^manual$")] = "manual"


class HumanAnnotation(ContractModel):
    id: UUID
    reference_set_id: UUID
    trace_id: str
    annotator_id: str
    label: HumanLabel
    rationale: str | None
    source: str
    created_at: datetime
    updated_at: datetime


class AnnotationListParams(PageParams):
    trace_id: TraceId | None = None
    annotator_id: AnnotatorId | None = None


class HumanAnnotationList(ContractModel):
    items: list[HumanAnnotation]
    has_more: bool


class ReferenceLabelWrite(ContractModel):
    label: HumanLabel
    annotator_id: AnnotatorId
    rationale: Rationale | None = None


class CalibrationStudyCreate(ContractModel):
    name: Name
    description: Description | None = None
    reference_set_id: UUID


class CalibrationStudy(ContractModel):
    id: UUID
    name: str
    description: str | None
    reference_set_id: UUID
    status: CalibrationStudyStatus
    subject_count: int
    created_at: datetime


class CalibrationStudyList(ContractModel):
    items: list[CalibrationStudy]
    has_more: bool


class CalibrationStudySubject(ContractModel):
    study_id: UUID
    trace_id: str
    position: int
    reference_label: HumanLabel


class CalibrationStudySubjectList(ContractModel):
    items: list[CalibrationStudySubject]
    has_more: bool
