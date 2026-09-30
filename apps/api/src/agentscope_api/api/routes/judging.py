from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from agentscope_api.core.config import get_settings
from agentscope_api.database import DatabaseSession
from agentscope_api.jobs.calibration import enqueue_judge_run
from agentscope_api.schemas.calibration import PageParams
from agentscope_api.schemas.calibration_analysis import (
    CalibrationAnalysis,
    CalibrationDisagreementList,
    DisagreementCategory,
    DisagreementListParams,
)
from agentscope_api.schemas.judging import (
    JudgeConfiguration,
    JudgeConfigurationCreate,
    JudgeConfigurationList,
    JudgeResultList,
    JudgeRun,
    JudgeRunCreate,
    JudgeRunList,
    JudgeRunProgress,
    JudgeRunStatus,
    JudgeRunSubmission,
)
from agentscope_api.services.calibration_analysis import (
    CalibrationAnalysisNotFound,
    IncompleteComparisonPopulation,
    JudgeRunNotCompleted,
    create_analysis,
    get_analysis,
    list_disagreements,
)
from agentscope_api.services.judge_orchestration import (
    JudgeRunNotExecutable,
    mark_judge_run_enqueued,
    submit_judge_run,
)
from agentscope_api.services.judging import (
    EmptyJudgeStudy,
    JudgeConfigurationNotFound,
    JudgeRunNotFound,
    JudgeStudyNotFound,
    create_configuration,
    create_run,
    get_configuration,
    get_progress,
    get_run,
    list_configurations,
    list_results,
    list_runs,
)

router = APIRouter(prefix="/api/v1", tags=["calibration-judging"])
logger = logging.getLogger(__name__)


def _not_found(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": code, "message": message})


def _persistence_error() -> HTTPException:
    return HTTPException(
        status_code=500,
        detail={"code": "PERSISTENCE_ERROR", "message": "judge persistence failed"},
    )


@router.post("/judge-configurations", response_model=JudgeConfiguration, status_code=201)
async def create_judge_configuration(
    payload: JudgeConfigurationCreate, session: DatabaseSession
) -> JudgeConfiguration:
    try:
        return await create_configuration(session, payload)
    except SQLAlchemyError as error:
        logger.error("judge_configuration_create_failed")
        raise _persistence_error() from error


@router.get("/judge-configurations", response_model=JudgeConfigurationList)
async def read_judge_configurations(
    session: DatabaseSession, params: Annotated[PageParams, Query()]
) -> JudgeConfigurationList:
    return await list_configurations(session, params)


@router.get("/judge-configurations/{configuration_id}", response_model=JudgeConfiguration)
async def read_judge_configuration(
    configuration_id: UUID, session: DatabaseSession
) -> JudgeConfiguration:
    result = await get_configuration(session, configuration_id)
    if result is None:
        raise _not_found("JUDGE_CONFIGURATION_NOT_FOUND", "judge configuration was not found")
    return result


@router.post("/calibration-judge-runs", response_model=JudgeRun, status_code=201)
async def create_judge_run(payload: JudgeRunCreate, session: DatabaseSession) -> JudgeRun:
    try:
        return await create_run(session, payload)
    except JudgeStudyNotFound as error:
        raise _not_found(
            "CALIBRATION_STUDY_NOT_FOUND", "calibration study was not found"
        ) from error
    except JudgeConfigurationNotFound as error:
        raise _not_found(
            "JUDGE_CONFIGURATION_NOT_FOUND", "judge configuration was not found"
        ) from error
    except EmptyJudgeStudy as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "CALIBRATION_STUDY_EMPTY", "message": "calibration study is empty"},
        ) from error
    except SQLAlchemyError as error:
        logger.error("judge_run_create_failed")
        raise _persistence_error() from error


@router.get("/calibration-judge-runs", response_model=JudgeRunList)
async def read_judge_runs(
    session: DatabaseSession, params: Annotated[PageParams, Query()]
) -> JudgeRunList:
    return await list_runs(session, params)


@router.get("/calibration-judge-runs/{run_id}", response_model=JudgeRun)
async def read_judge_run(run_id: UUID, session: DatabaseSession) -> JudgeRun:
    result = await get_run(session, run_id)
    if result is None:
        raise _not_found("JUDGE_RUN_NOT_FOUND", "judge run was not found")
    return result


@router.post(
    "/calibration-judge-runs/{run_id}/execute",
    response_model=JudgeRunSubmission,
    status_code=202,
)
async def execute_judge_run(run_id: UUID, session: DatabaseSession) -> JudgeRunSubmission:
    if get_settings().openai_api_key is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "OPENAI_NOT_CONFIGURED", "message": "OpenAI judge is not configured"},
        )
    try:
        await submit_judge_run(session, run_id)
    except JudgeRunNotFound as error:
        raise _not_found("JUDGE_RUN_NOT_FOUND", "judge run was not found") from error
    except JudgeRunNotExecutable as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "JUDGE_RUN_NOT_EXECUTABLE", "message": "judge run cannot be executed"},
        ) from error
    try:
        enqueue_judge_run(run_id)
        await mark_judge_run_enqueued(session, run_id)
    except Exception:
        logger.warning("judge_enqueue_deferred run_id=%s", run_id)
    return JudgeRunSubmission(run_id=run_id, status=JudgeRunStatus.QUEUED)


@router.get("/calibration-judge-runs/{run_id}/progress", response_model=JudgeRunProgress)
async def read_judge_run_progress(run_id: UUID, session: DatabaseSession) -> JudgeRunProgress:
    try:
        return await get_progress(session, run_id)
    except JudgeRunNotFound as error:
        raise _not_found("JUDGE_RUN_NOT_FOUND", "judge run was not found") from error


@router.get("/calibration-judge-runs/{run_id}/results", response_model=JudgeResultList)
async def read_judge_results(
    run_id: UUID, session: DatabaseSession, params: Annotated[PageParams, Query()]
) -> JudgeResultList:
    try:
        return await list_results(session, run_id, params)
    except JudgeRunNotFound as error:
        raise _not_found("JUDGE_RUN_NOT_FOUND", "judge run was not found") from error


@router.post(
    "/calibration-judge-runs/{run_id}/analysis",
    response_model=CalibrationAnalysis,
    status_code=201,
)
async def create_calibration_analysis(
    run_id: UUID, session: DatabaseSession
) -> CalibrationAnalysis:
    try:
        return await create_analysis(session, run_id)
    except JudgeRunNotFound as error:
        raise _not_found("JUDGE_RUN_NOT_FOUND", "judge run was not found") from error
    except JudgeRunNotCompleted as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "JUDGE_RUN_NOT_COMPLETED",
                "message": "judge run did not complete successfully",
            },
        ) from error
    except IncompleteComparisonPopulation as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "INCOMPLETE_COMPARISON_POPULATION",
                "message": "judge results do not exactly match the study population",
            },
        ) from error
    except SQLAlchemyError as error:
        logger.error("calibration_analysis_create_failed run_id=%s", run_id)
        raise _persistence_error() from error


@router.get(
    "/calibration-judge-runs/{run_id}/analysis", response_model=CalibrationAnalysis
)
async def read_calibration_analysis(
    run_id: UUID, session: DatabaseSession
) -> CalibrationAnalysis:
    try:
        return await get_analysis(session, run_id)
    except JudgeRunNotFound as error:
        raise _not_found("JUDGE_RUN_NOT_FOUND", "judge run was not found") from error
    except CalibrationAnalysisNotFound as error:
        raise _not_found(
            "CALIBRATION_ANALYSIS_NOT_FOUND", "calibration analysis was not found"
        ) from error


@router.get(
    "/calibration-judge-runs/{run_id}/disagreements",
    response_model=CalibrationDisagreementList,
)
async def read_calibration_disagreements(
    run_id: UUID,
    session: DatabaseSession,
    params: Annotated[DisagreementListParams, Query()],
) -> CalibrationDisagreementList:
    try:
        parsed_category = DisagreementCategory(params.category) if params.category else None
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_DISAGREEMENT_FILTER",
                "message": "category must be false_positive or false_negative",
            },
        ) from error
    try:
        return await list_disagreements(session, run_id, params, parsed_category)
    except JudgeRunNotFound as error:
        raise _not_found("JUDGE_RUN_NOT_FOUND", "judge run was not found") from error
    except CalibrationAnalysisNotFound as error:
        raise _not_found(
            "CALIBRATION_ANALYSIS_NOT_FOUND", "calibration analysis was not found"
        ) from error
