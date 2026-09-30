from __future__ import annotations

import logging
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agentscope_api.database import DatabaseSession
from agentscope_api.schemas.calibration import (
    AnnotationListParams,
    CalibrationStudy,
    CalibrationStudyCreate,
    CalibrationStudyList,
    CalibrationStudySubjectList,
    HumanAnnotation,
    HumanAnnotationList,
    HumanAnnotationWrite,
    HumanReferenceSet,
    HumanReferenceSetCreate,
    HumanReferenceSetList,
    HumanReferenceSubjectList,
    PageParams,
    ReferenceLabelWrite,
    ReferenceSubjectsDefine,
    TraceId,
)
from agentscope_api.services.calibration import (
    CalibrationStudyNotFound,
    HumanAnnotationNotFound,
    IncompleteReferenceSet,
    ReferenceSetConflict,
    ReferenceSetNotFound,
    ReferenceSubjectNotFound,
    TraceNotFound,
    begin_labeling,
    create_reference_set,
    create_study,
    define_subjects,
    freeze_reference_set,
    get_annotation,
    get_reference_set,
    get_study,
    list_annotations,
    list_reference_sets,
    list_studies,
    list_study_subjects,
    list_subjects,
    set_reference_label,
    write_annotation,
)

router = APIRouter(prefix="/api/v1", tags=["calibration"])
logger = logging.getLogger(__name__)


def _raise_domain(error: Exception) -> NoReturn:
    if isinstance(error, ReferenceSetNotFound):
        code, message, status = "REFERENCE_SET_NOT_FOUND", "reference set was not found", 404
    elif isinstance(error, ReferenceSubjectNotFound):
        code, message, status = (
            "REFERENCE_SUBJECT_NOT_FOUND",
            "reference subject was not found",
            404,
        )
    elif isinstance(error, CalibrationStudyNotFound):
        code, message, status = (
            "CALIBRATION_STUDY_NOT_FOUND",
            "calibration study was not found",
            404,
        )
    elif isinstance(error, HumanAnnotationNotFound):
        code, message, status = "HUMAN_ANNOTATION_NOT_FOUND", "annotation was not found", 404
    elif isinstance(error, TraceNotFound):
        code, message, status = "TRACE_NOT_FOUND", "one or more traces were not found", 404
    elif isinstance(error, IncompleteReferenceSet):
        code, message, status = (
            "REFERENCE_SET_INCOMPLETE",
            "reference set is not complete for this operation",
            409,
        )
    else:
        code, message, status = (
            "REFERENCE_SET_CONFLICT",
            "operation conflicts with the reference set lifecycle",
            409,
        )
    raise HTTPException(status_code=status, detail={"code": code, "message": message}) from error


def _persistence_error() -> HTTPException:
    return HTTPException(
        status_code=500,
        detail={"code": "PERSISTENCE_ERROR", "message": "calibration persistence failed"},
    )


async def _read_set_or_404(session: DatabaseSession, reference_set_id: UUID) -> HumanReferenceSet:
    result = await get_reference_set(session, reference_set_id)
    if result is None:
        _raise_domain(ReferenceSetNotFound())
    return result


@router.post("/human-reference-sets", response_model=HumanReferenceSet, status_code=201)
async def create_human_reference_set(
    payload: HumanReferenceSetCreate, session: DatabaseSession
) -> HumanReferenceSet:
    try:
        return await create_reference_set(session, payload)
    except SQLAlchemyError as error:
        logger.error("reference_set_create_failed")
        raise _persistence_error() from error


@router.get("/human-reference-sets", response_model=HumanReferenceSetList)
async def read_human_reference_sets(
    session: DatabaseSession, params: Annotated[PageParams, Query()]
) -> HumanReferenceSetList:
    try:
        return await list_reference_sets(session, params)
    except SQLAlchemyError as error:
        logger.error("reference_set_list_failed")
        raise _persistence_error() from error


@router.get("/human-reference-sets/{reference_set_id}", response_model=HumanReferenceSet)
async def read_human_reference_set(
    reference_set_id: UUID, session: DatabaseSession
) -> HumanReferenceSet:
    try:
        return await _read_set_or_404(session, reference_set_id)
    except SQLAlchemyError as error:
        logger.error("reference_set_read_failed")
        raise _persistence_error() from error


@router.post("/human-reference-sets/{reference_set_id}/subjects", response_model=HumanReferenceSet)
async def define_human_reference_subjects(
    reference_set_id: UUID, payload: ReferenceSubjectsDefine, session: DatabaseSession
) -> HumanReferenceSet:
    try:
        await define_subjects(session, reference_set_id, payload)
        return await _read_set_or_404(session, reference_set_id)
    except (ReferenceSetNotFound, TraceNotFound, ReferenceSetConflict) as error:
        _raise_domain(error)
    except IntegrityError:
        _raise_domain(ReferenceSetConflict())
    except SQLAlchemyError as error:
        logger.error("reference_subjects_define_failed")
        raise _persistence_error() from error


@router.get(
    "/human-reference-sets/{reference_set_id}/subjects",
    response_model=HumanReferenceSubjectList,
)
async def read_human_reference_subjects(
    reference_set_id: UUID,
    session: DatabaseSession,
    params: Annotated[PageParams, Query()],
) -> HumanReferenceSubjectList:
    try:
        return await list_subjects(session, reference_set_id, params)
    except ReferenceSetNotFound as error:
        _raise_domain(error)
    except SQLAlchemyError as error:
        logger.error("reference_subjects_list_failed")
        raise _persistence_error() from error


@router.post(
    "/human-reference-sets/{reference_set_id}/begin-labeling",
    response_model=HumanReferenceSet,
)
async def begin_human_reference_labeling(
    reference_set_id: UUID, session: DatabaseSession
) -> HumanReferenceSet:
    try:
        await begin_labeling(session, reference_set_id)
        return await _read_set_or_404(session, reference_set_id)
    except (ReferenceSetNotFound, ReferenceSetConflict, IncompleteReferenceSet) as error:
        _raise_domain(error)
    except SQLAlchemyError as error:
        logger.error("reference_set_begin_failed")
        raise _persistence_error() from error


@router.post("/human-reference-sets/{reference_set_id}/annotations", response_model=HumanAnnotation)
async def write_human_annotation(
    reference_set_id: UUID, payload: HumanAnnotationWrite, session: DatabaseSession
) -> HumanAnnotation:
    try:
        return HumanAnnotation.model_validate(
            await write_annotation(session, reference_set_id, payload)
        )
    except (ReferenceSetNotFound, ReferenceSubjectNotFound, ReferenceSetConflict) as error:
        _raise_domain(error)
    except IntegrityError:
        _raise_domain(ReferenceSetConflict())
    except SQLAlchemyError as error:
        logger.error("human_annotation_write_failed")
        raise _persistence_error() from error


@router.get(
    "/human-reference-sets/{reference_set_id}/annotations", response_model=HumanAnnotationList
)
async def read_human_annotations(
    reference_set_id: UUID,
    session: DatabaseSession,
    params: Annotated[AnnotationListParams, Query()],
) -> HumanAnnotationList:
    try:
        return await list_annotations(session, reference_set_id, params)
    except ReferenceSetNotFound as error:
        _raise_domain(error)
    except SQLAlchemyError as error:
        logger.error("human_annotations_list_failed")
        raise _persistence_error() from error


@router.get("/human-annotations/{annotation_id}", response_model=HumanAnnotation)
async def read_human_annotation(annotation_id: UUID, session: DatabaseSession) -> HumanAnnotation:
    try:
        record = await get_annotation(session, annotation_id)
    except SQLAlchemyError as error:
        logger.error("human_annotation_read_failed")
        raise _persistence_error() from error
    if record is None:
        _raise_domain(HumanAnnotationNotFound())
    return HumanAnnotation.model_validate(record)


@router.post(
    "/human-reference-sets/{reference_set_id}/subjects/{trace_id}/reference-label",
    status_code=204,
)
async def write_reference_label(
    reference_set_id: UUID,
    trace_id: TraceId,
    payload: ReferenceLabelWrite,
    session: DatabaseSession,
) -> Response:
    try:
        await set_reference_label(session, reference_set_id, trace_id, payload)
        return Response(status_code=204)
    except (ReferenceSetNotFound, ReferenceSubjectNotFound, ReferenceSetConflict) as error:
        _raise_domain(error)
    except SQLAlchemyError as error:
        logger.error("reference_label_write_failed")
        raise _persistence_error() from error


@router.post("/human-reference-sets/{reference_set_id}/freeze", response_model=HumanReferenceSet)
async def freeze_human_reference_set(
    reference_set_id: UUID, session: DatabaseSession
) -> HumanReferenceSet:
    try:
        await freeze_reference_set(session, reference_set_id)
        return await _read_set_or_404(session, reference_set_id)
    except (ReferenceSetNotFound, ReferenceSetConflict, IncompleteReferenceSet) as error:
        _raise_domain(error)
    except SQLAlchemyError as error:
        logger.error("reference_set_freeze_failed")
        raise _persistence_error() from error


@router.post("/calibration-studies", response_model=CalibrationStudy, status_code=201)
async def create_calibration_study(
    payload: CalibrationStudyCreate, session: DatabaseSession
) -> CalibrationStudy:
    try:
        return await create_study(session, payload)
    except (ReferenceSetNotFound, IncompleteReferenceSet) as error:
        _raise_domain(error)
    except SQLAlchemyError as error:
        logger.error("calibration_study_create_failed")
        raise _persistence_error() from error


@router.get("/calibration-studies", response_model=CalibrationStudyList)
async def read_calibration_studies(
    session: DatabaseSession, params: Annotated[PageParams, Query()]
) -> CalibrationStudyList:
    try:
        return await list_studies(session, params)
    except SQLAlchemyError as error:
        logger.error("calibration_study_list_failed")
        raise _persistence_error() from error


@router.get("/calibration-studies/{study_id}", response_model=CalibrationStudy)
async def read_calibration_study(study_id: UUID, session: DatabaseSession) -> CalibrationStudy:
    try:
        result = await get_study(session, study_id)
    except SQLAlchemyError as error:
        logger.error("calibration_study_read_failed")
        raise _persistence_error() from error
    if result is None:
        _raise_domain(CalibrationStudyNotFound())
    return result


@router.get("/calibration-studies/{study_id}/subjects", response_model=CalibrationStudySubjectList)
async def read_calibration_study_subjects(
    study_id: UUID, session: DatabaseSession, params: Annotated[PageParams, Query()]
) -> CalibrationStudySubjectList:
    try:
        return await list_study_subjects(session, study_id, params)
    except CalibrationStudyNotFound as error:
        _raise_domain(error)
    except SQLAlchemyError as error:
        logger.error("calibration_study_subjects_list_failed")
        raise _persistence_error() from error
