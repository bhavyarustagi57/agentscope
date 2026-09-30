from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from agentscope_api.models.calibration import (
    CalibrationStudyRecord,
    CalibrationStudySubjectRecord,
    HumanAnnotationRecord,
    HumanReferenceSetRecord,
    HumanReferenceSubjectRecord,
)
from agentscope_api.models.trace import TraceRecord
from agentscope_api.schemas.calibration import (
    AnnotationListParams,
    CalibrationStudy,
    CalibrationStudyCreate,
    CalibrationStudyList,
    CalibrationStudySubject,
    CalibrationStudySubjectList,
    HumanAnnotation,
    HumanAnnotationList,
    HumanAnnotationWrite,
    HumanReferenceSet,
    HumanReferenceSetCreate,
    HumanReferenceSetList,
    HumanReferenceSubject,
    HumanReferenceSubjectList,
    PageParams,
    ReferenceLabelWrite,
    ReferenceSetStatus,
    ReferenceSubjectsDefine,
)


class ReferenceSetNotFound(Exception):
    pass


class ReferenceSubjectNotFound(Exception):
    pass


class HumanAnnotationNotFound(Exception):
    pass


class CalibrationStudyNotFound(Exception):
    pass


class TraceNotFound(Exception):
    pass


class ReferenceSetConflict(Exception):
    pass


class IncompleteReferenceSet(Exception):
    pass


async def create_reference_set(
    session: AsyncSession, payload: HumanReferenceSetCreate
) -> HumanReferenceSet:
    record = HumanReferenceSetRecord(**payload.model_dump(mode="python"))
    async with session.begin():
        session.add(record)
    result = await get_reference_set(session, record.id)
    assert result is not None
    return result


def _reference_set_statement() -> Select[Any]:
    subjects = (
        select(
            HumanReferenceSubjectRecord.reference_set_id.label("reference_set_id"),
            func.count().label("subject_count"),
            func.count(HumanReferenceSubjectRecord.reference_label).label("reference_count"),
            func.count()
            .filter(HumanReferenceSubjectRecord.reference_label == "passed")
            .label("passed_count"),
            func.count()
            .filter(HumanReferenceSubjectRecord.reference_label == "failed")
            .label("failed_count"),
        )
        .group_by(HumanReferenceSubjectRecord.reference_set_id)
        .subquery()
    )
    annotations = (
        select(
            HumanAnnotationRecord.reference_set_id.label("reference_set_id"),
            func.count().label("annotation_count"),
        )
        .group_by(HumanAnnotationRecord.reference_set_id)
        .subquery()
    )
    return (
        select(
            HumanReferenceSetRecord,
            func.coalesce(subjects.c.subject_count, 0).label("subject_count"),
            func.coalesce(annotations.c.annotation_count, 0).label("annotation_count"),
            func.coalesce(subjects.c.reference_count, 0).label("reference_count"),
            (
                func.coalesce(subjects.c.subject_count, 0)
                - func.coalesce(subjects.c.reference_count, 0)
            ).label("unlabeled_count"),
            func.coalesce(subjects.c.passed_count, 0).label("passed_count"),
            func.coalesce(subjects.c.failed_count, 0).label("failed_count"),
        )
        .outerjoin(subjects, subjects.c.reference_set_id == HumanReferenceSetRecord.id)
        .outerjoin(annotations, annotations.c.reference_set_id == HumanReferenceSetRecord.id)
    )


def _reference_set(row: Row[Any]) -> HumanReferenceSet:
    return HumanReferenceSet.model_validate(row[0]).model_copy(
        update={
            "subject_count": row.subject_count,
            "annotation_count": row.annotation_count,
            "reference_count": row.reference_count,
            "unlabeled_count": row.unlabeled_count,
            "passed_count": row.passed_count,
            "failed_count": row.failed_count,
        }
    )


async def get_reference_set(
    session: AsyncSession, reference_set_id: UUID
) -> HumanReferenceSet | None:
    row = (
        await session.execute(
            _reference_set_statement().where(HumanReferenceSetRecord.id == reference_set_id)
        )
    ).one_or_none()
    return None if row is None else _reference_set(row)


async def list_reference_sets(session: AsyncSession, params: PageParams) -> HumanReferenceSetList:
    rows = (
        await session.execute(
            _reference_set_statement()
            .order_by(HumanReferenceSetRecord.created_at.desc(), HumanReferenceSetRecord.id.desc())
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    ).all()
    return HumanReferenceSetList(
        items=[_reference_set(row) for row in rows[: params.page_size]],
        has_more=len(rows) > params.page_size,
    )


async def define_subjects(
    session: AsyncSession, reference_set_id: UUID, payload: ReferenceSubjectsDefine
) -> None:
    async with session.begin():
        reference_set = await session.scalar(
            select(HumanReferenceSetRecord)
            .where(HumanReferenceSetRecord.id == reference_set_id)
            .with_for_update()
        )
        if reference_set is None:
            raise ReferenceSetNotFound
        if reference_set.status != ReferenceSetStatus.DRAFT.value:
            raise ReferenceSetConflict
        stored = list(
            await session.scalars(
                select(HumanReferenceSubjectRecord.trace_id)
                .where(HumanReferenceSubjectRecord.reference_set_id == reference_set_id)
                .order_by(HumanReferenceSubjectRecord.position)
            )
        )
        if stored:
            if stored == payload.trace_ids:
                return
            raise ReferenceSetConflict
        existing = set(
            await session.scalars(
                select(TraceRecord.trace_id).where(TraceRecord.trace_id.in_(payload.trace_ids))
            )
        )
        if len(existing) != len(payload.trace_ids):
            raise TraceNotFound
        session.add_all(
            HumanReferenceSubjectRecord(
                reference_set_id=reference_set_id, trace_id=trace_id, position=position
            )
            for position, trace_id in enumerate(payload.trace_ids)
        )


def _subject(row: Row[Any]) -> HumanReferenceSubject:
    record = row[0]
    return HumanReferenceSubject(
        reference_set_id=record.reference_set_id,
        trace_id=record.trace_id,
        position=record.position,
        trace_name=row.trace_name,
        reference_label=record.reference_label,
        reference_rationale=record.reference_rationale,
        reference_annotator_id=record.reference_annotator_id,
        reference_labeled_at=record.reference_labeled_at,
        annotation_count=row.annotation_count,
        created_at=record.created_at,
    )


async def list_subjects(
    session: AsyncSession, reference_set_id: UUID, params: PageParams
) -> HumanReferenceSubjectList:
    if await session.get(HumanReferenceSetRecord, reference_set_id) is None:
        raise ReferenceSetNotFound
    annotations = (
        select(
            HumanAnnotationRecord.reference_set_id,
            HumanAnnotationRecord.trace_id,
            func.count().label("annotation_count"),
        )
        .group_by(HumanAnnotationRecord.reference_set_id, HumanAnnotationRecord.trace_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(
                HumanReferenceSubjectRecord,
                TraceRecord.name.label("trace_name"),
                func.coalesce(annotations.c.annotation_count, 0).label("annotation_count"),
            )
            .join(TraceRecord, TraceRecord.trace_id == HumanReferenceSubjectRecord.trace_id)
            .outerjoin(
                annotations,
                (annotations.c.reference_set_id == HumanReferenceSubjectRecord.reference_set_id)
                & (annotations.c.trace_id == HumanReferenceSubjectRecord.trace_id),
            )
            .where(HumanReferenceSubjectRecord.reference_set_id == reference_set_id)
            .order_by(HumanReferenceSubjectRecord.position)
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    ).all()
    return HumanReferenceSubjectList(
        items=[_subject(row) for row in rows[: params.page_size]],
        has_more=len(rows) > params.page_size,
    )


async def begin_labeling(session: AsyncSession, reference_set_id: UUID) -> None:
    async with session.begin():
        reference_set = await session.scalar(
            select(HumanReferenceSetRecord)
            .where(HumanReferenceSetRecord.id == reference_set_id)
            .with_for_update()
        )
        if reference_set is None:
            raise ReferenceSetNotFound
        if reference_set.status == ReferenceSetStatus.LABELING.value:
            return
        if reference_set.status != ReferenceSetStatus.DRAFT.value:
            raise ReferenceSetConflict
        count = await session.scalar(
            select(func.count())
            .select_from(HumanReferenceSubjectRecord)
            .where(HumanReferenceSubjectRecord.reference_set_id == reference_set_id)
        )
        if not count:
            raise IncompleteReferenceSet
        reference_set.status = ReferenceSetStatus.LABELING.value


async def write_annotation(
    session: AsyncSession, reference_set_id: UUID, payload: HumanAnnotationWrite
) -> HumanAnnotationRecord:
    async with session.begin():
        reference_set = await session.scalar(
            select(HumanReferenceSetRecord)
            .where(HumanReferenceSetRecord.id == reference_set_id)
            .with_for_update()
        )
        if reference_set is None:
            raise ReferenceSetNotFound
        if reference_set.status != ReferenceSetStatus.LABELING.value:
            raise ReferenceSetConflict
        if (
            await session.get(HumanReferenceSubjectRecord, (reference_set_id, payload.trace_id))
            is None
        ):
            raise ReferenceSubjectNotFound
        record = await session.scalar(
            select(HumanAnnotationRecord).where(
                HumanAnnotationRecord.reference_set_id == reference_set_id,
                HumanAnnotationRecord.trace_id == payload.trace_id,
                HumanAnnotationRecord.annotator_id == payload.annotator_id,
            )
        )
        values = payload.model_dump(mode="python", exclude={"trace_id"})
        if record is None:
            record = HumanAnnotationRecord(
                reference_set_id=reference_set_id, trace_id=payload.trace_id, **values
            )
            session.add(record)
        else:
            record.label = payload.label.value
            record.rationale = payload.rationale
            record.source = payload.source
    await session.refresh(record)
    return record


async def list_annotations(
    session: AsyncSession, reference_set_id: UUID, params: AnnotationListParams
) -> HumanAnnotationList:
    if await session.get(HumanReferenceSetRecord, reference_set_id) is None:
        raise ReferenceSetNotFound
    statement = select(HumanAnnotationRecord).where(
        HumanAnnotationRecord.reference_set_id == reference_set_id
    )
    if params.trace_id is not None:
        statement = statement.where(HumanAnnotationRecord.trace_id == params.trace_id)
    if params.annotator_id is not None:
        statement = statement.where(HumanAnnotationRecord.annotator_id == params.annotator_id)
    rows = (
        await session.scalars(
            statement.order_by(
                HumanAnnotationRecord.created_at.asc(), HumanAnnotationRecord.id.asc()
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    ).all()
    return HumanAnnotationList(
        items=[HumanAnnotation.model_validate(row) for row in rows[: params.page_size]],
        has_more=len(rows) > params.page_size,
    )


async def get_annotation(
    session: AsyncSession, annotation_id: UUID
) -> HumanAnnotationRecord | None:
    return await session.get(HumanAnnotationRecord, annotation_id)


async def set_reference_label(
    session: AsyncSession,
    reference_set_id: UUID,
    trace_id: str,
    payload: ReferenceLabelWrite,
) -> None:
    async with session.begin():
        reference_set = await session.scalar(
            select(HumanReferenceSetRecord)
            .where(HumanReferenceSetRecord.id == reference_set_id)
            .with_for_update()
        )
        if reference_set is None:
            raise ReferenceSetNotFound
        if reference_set.status != ReferenceSetStatus.LABELING.value:
            raise ReferenceSetConflict
        subject = await session.get(HumanReferenceSubjectRecord, (reference_set_id, trace_id))
        if subject is None:
            raise ReferenceSubjectNotFound
        if (
            subject.reference_label == payload.label.value
            and subject.reference_rationale == payload.rationale
            and subject.reference_annotator_id == payload.annotator_id
        ):
            return
        subject.reference_label = payload.label.value
        subject.reference_rationale = payload.rationale
        subject.reference_annotator_id = payload.annotator_id
        subject.reference_labeled_at = datetime.now(UTC)


async def freeze_reference_set(session: AsyncSession, reference_set_id: UUID) -> None:
    async with session.begin():
        reference_set = await session.scalar(
            select(HumanReferenceSetRecord)
            .where(HumanReferenceSetRecord.id == reference_set_id)
            .with_for_update()
        )
        if reference_set is None:
            raise ReferenceSetNotFound
        if reference_set.status == ReferenceSetStatus.FROZEN.value:
            return
        if reference_set.status != ReferenceSetStatus.LABELING.value:
            raise ReferenceSetConflict
        counts = (
            await session.execute(
                select(func.count(), func.count(HumanReferenceSubjectRecord.reference_label)).where(
                    HumanReferenceSubjectRecord.reference_set_id == reference_set_id
                )
            )
        ).one()
        if counts[0] == 0 or counts[0] != counts[1]:
            raise IncompleteReferenceSet
        now = datetime.now(UTC)
        reference_set.status = ReferenceSetStatus.FROZEN.value
        reference_set.frozen_at = now
        reference_set.updated_at = now


async def create_study(session: AsyncSession, payload: CalibrationStudyCreate) -> CalibrationStudy:
    async with session.begin():
        reference_set = await session.scalar(
            select(HumanReferenceSetRecord)
            .where(HumanReferenceSetRecord.id == payload.reference_set_id)
            .with_for_update()
        )
        if reference_set is None:
            raise ReferenceSetNotFound
        if reference_set.status != ReferenceSetStatus.FROZEN.value:
            raise IncompleteReferenceSet
        subjects = (
            await session.execute(
                select(
                    HumanReferenceSubjectRecord.trace_id,
                    HumanReferenceSubjectRecord.position,
                    HumanReferenceSubjectRecord.reference_label,
                )
                .where(HumanReferenceSubjectRecord.reference_set_id == payload.reference_set_id)
                .order_by(HumanReferenceSubjectRecord.position)
            )
        ).all()
        if not subjects or any(row.reference_label is None for row in subjects):
            raise IncompleteReferenceSet
        record = CalibrationStudyRecord(**payload.model_dump(mode="python"))
        session.add(record)
        await session.flush()
        session.add_all(
            CalibrationStudySubjectRecord(
                study_id=record.id,
                trace_id=row.trace_id,
                position=row.position,
                reference_label=row.reference_label,
            )
            for row in subjects
        )
    return CalibrationStudy(
        id=record.id,
        name=record.name,
        description=record.description,
        reference_set_id=record.reference_set_id,
        status=record.status,
        subject_count=len(subjects),
        created_at=record.created_at,
    )


def _study_statement() -> Select[Any]:
    counts = (
        select(
            CalibrationStudySubjectRecord.study_id,
            func.count().label("subject_count"),
        )
        .group_by(CalibrationStudySubjectRecord.study_id)
        .subquery()
    )
    return select(
        CalibrationStudyRecord, func.coalesce(counts.c.subject_count, 0).label("subject_count")
    ).outerjoin(counts, counts.c.study_id == CalibrationStudyRecord.id)


def _study(row: Row[Any]) -> CalibrationStudy:
    record = row[0]
    return CalibrationStudy(
        id=record.id,
        name=record.name,
        description=record.description,
        reference_set_id=record.reference_set_id,
        status=record.status,
        subject_count=row.subject_count,
        created_at=record.created_at,
    )


async def get_study(session: AsyncSession, study_id: UUID) -> CalibrationStudy | None:
    row = (
        await session.execute(_study_statement().where(CalibrationStudyRecord.id == study_id))
    ).one_or_none()
    return None if row is None else _study(row)


async def list_studies(session: AsyncSession, params: PageParams) -> CalibrationStudyList:
    rows = (
        await session.execute(
            _study_statement()
            .order_by(CalibrationStudyRecord.created_at.desc(), CalibrationStudyRecord.id.desc())
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    ).all()
    return CalibrationStudyList(
        items=[_study(row) for row in rows[: params.page_size]],
        has_more=len(rows) > params.page_size,
    )


async def list_study_subjects(
    session: AsyncSession, study_id: UUID, params: PageParams
) -> CalibrationStudySubjectList:
    if await session.get(CalibrationStudyRecord, study_id) is None:
        raise CalibrationStudyNotFound
    rows = (
        await session.scalars(
            select(CalibrationStudySubjectRecord)
            .where(CalibrationStudySubjectRecord.study_id == study_id)
            .order_by(CalibrationStudySubjectRecord.position)
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    ).all()
    return CalibrationStudySubjectList(
        items=[CalibrationStudySubject.model_validate(row) for row in rows[: params.page_size]],
        has_more=len(rows) > params.page_size,
    )
