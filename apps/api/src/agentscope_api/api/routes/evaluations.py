from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agentscope_api.database import DatabaseSession
from agentscope_api.jobs.evaluations import enqueue_evaluation_run
from agentscope_api.schemas.evaluations import (
    DefinitionListParams,
    EvaluationDefinition,
    EvaluationDefinitionCreate,
    EvaluationDefinitionList,
    EvaluationResult,
    EvaluationResultList,
    EvaluationRun,
    EvaluationRunCreate,
    EvaluationRunExecution,
    EvaluationRunExecutionAccepted,
    EvaluationRunList,
    ResultListParams,
    RunListParams,
)
from agentscope_api.services.evaluation_execution import ExistingEvaluationResults
from agentscope_api.services.evaluation_orchestration import (
    ExecutionIntentConflict,
    ExecutionNotAllowed,
    mark_run_enqueued,
    submit_evaluation_run,
)
from agentscope_api.services.evaluations import (
    EvaluationDefinitionDisabled,
    EvaluationDefinitionNotFound,
    EvaluationRunNotFound,
    InvalidEvaluationRunState,
    TraceNotFound,
    create_definition,
    create_run,
    get_definition,
    get_result,
    get_run,
    list_definitions,
    list_results,
    list_runs,
)

router = APIRouter(prefix="/api/v1", tags=["evaluations"])
logger = logging.getLogger(__name__)


def _not_found(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": code, "message": message})


def _persistence_error() -> HTTPException:
    return HTTPException(
        status_code=500,
        detail={"code": "PERSISTENCE_ERROR", "message": "evaluation persistence failed"},
    )


@router.post("/evaluation-definitions", response_model=EvaluationDefinition, status_code=201)
async def create_evaluation_definition(
    payload: EvaluationDefinitionCreate, session: DatabaseSession
) -> EvaluationDefinition:
    try:
        return EvaluationDefinition.model_validate(await create_definition(session, payload))
    except IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "EVALUATION_CONFLICT", "message": "evaluation definition conflicts"},
        ) from error
    except SQLAlchemyError as error:
        logger.error("evaluation_definition_create_failed")
        raise _persistence_error() from error


@router.get("/evaluation-definitions", response_model=EvaluationDefinitionList)
async def read_evaluation_definitions(
    session: DatabaseSession,
    params: Annotated[DefinitionListParams, Query()],
) -> EvaluationDefinitionList:
    try:
        return await list_definitions(session, params)
    except SQLAlchemyError as error:
        logger.error("evaluation_definition_list_failed")
        raise _persistence_error() from error


@router.get("/evaluation-definitions/{definition_id}", response_model=EvaluationDefinition)
async def read_evaluation_definition(
    definition_id: UUID, session: DatabaseSession
) -> EvaluationDefinition:
    try:
        record = await get_definition(session, definition_id)
    except SQLAlchemyError as error:
        logger.error("evaluation_definition_read_failed")
        raise _persistence_error() from error
    if record is None:
        raise _not_found("EVALUATION_DEFINITION_NOT_FOUND", "evaluation definition was not found")
    return EvaluationDefinition.model_validate(record)


@router.post("/evaluation-runs", response_model=EvaluationRun, status_code=201)
async def create_evaluation_run(
    payload: EvaluationRunCreate, session: DatabaseSession
) -> EvaluationRun:
    try:
        return EvaluationRun.model_validate(await create_run(session, payload))
    except EvaluationDefinitionNotFound as error:
        raise _not_found(
            "EVALUATION_DEFINITION_NOT_FOUND", "evaluation definition was not found"
        ) from error
    except EvaluationDefinitionDisabled as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EVALUATION_DEFINITION_DISABLED",
                "message": "evaluation definition is disabled",
            },
        ) from error
    except IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "EVALUATION_CONFLICT", "message": "evaluation run conflicts"},
        ) from error
    except SQLAlchemyError as error:
        logger.error("evaluation_run_create_failed")
        raise _persistence_error() from error


@router.post(
    "/evaluation-runs/{run_id}/execute",
    response_model=EvaluationRunExecutionAccepted,
    status_code=202,
)
async def execute_evaluation_run(
    run_id: UUID, payload: EvaluationRunExecution, session: DatabaseSession
) -> EvaluationRunExecutionAccepted:
    try:
        submission = await submit_evaluation_run(session, run_id, payload)
    except EvaluationRunNotFound as error:
        raise _not_found("EVALUATION_RUN_NOT_FOUND", "evaluation run was not found") from error
    except TraceNotFound as error:
        raise _not_found("TRACE_NOT_FOUND", "one or more traces were not found") from error
    except (ExecutionIntentConflict, ExistingEvaluationResults) as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EVALUATION_EXECUTION_CONFLICT",
                "message": "evaluation execution intent conflicts",
            },
        ) from error
    except (ExecutionNotAllowed, InvalidEvaluationRunState) as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EVALUATION_RUN_NOT_EXECUTABLE",
                "message": "evaluation run is not executable",
            },
        ) from error
    except SQLAlchemyError as error:
        logger.error("evaluation_execution_submit_failed run_id=%s", run_id)
        raise _persistence_error() from error

    delivery: Literal["enqueued", "deferred"] = "enqueued"
    try:
        await asyncio.to_thread(enqueue_evaluation_run, run_id)
    except Exception:
        delivery = "deferred"
        logger.warning("evaluation_enqueue_deferred run_id=%s", run_id)
    else:
        try:
            await mark_run_enqueued(session, run_id)
        except SQLAlchemyError:
            logger.warning("evaluation_enqueue_marker_failed run_id=%s", run_id)

    return EvaluationRunExecutionAccepted(
        run_id=run_id,
        subject_count=submission.subject_count,
        queue_delivery=delivery,
    )


@router.get("/evaluation-runs", response_model=EvaluationRunList)
async def read_evaluation_runs(
    session: DatabaseSession,
    params: Annotated[RunListParams, Query()],
) -> EvaluationRunList:
    try:
        return await list_runs(session, params)
    except SQLAlchemyError as error:
        logger.error("evaluation_run_list_failed")
        raise _persistence_error() from error


@router.get("/evaluation-runs/{run_id}", response_model=EvaluationRun)
async def read_evaluation_run(run_id: UUID, session: DatabaseSession) -> EvaluationRun:
    try:
        run = await get_run(session, run_id)
    except SQLAlchemyError as error:
        logger.error("evaluation_run_read_failed")
        raise _persistence_error() from error
    if run is None:
        raise _not_found("EVALUATION_RUN_NOT_FOUND", "evaluation run was not found")
    return run


@router.get("/evaluation-runs/{run_id}/results", response_model=EvaluationResultList)
async def read_evaluation_results(
    run_id: UUID,
    session: DatabaseSession,
    params: Annotated[ResultListParams, Query()],
) -> EvaluationResultList:
    try:
        return await list_results(session, run_id, params)
    except EvaluationRunNotFound as error:
        raise _not_found("EVALUATION_RUN_NOT_FOUND", "evaluation run was not found") from error
    except SQLAlchemyError as error:
        logger.error("evaluation_result_list_failed")
        raise _persistence_error() from error


@router.get("/evaluation-results/{result_id}", response_model=EvaluationResult)
async def read_evaluation_result(result_id: UUID, session: DatabaseSession) -> EvaluationResult:
    try:
        result = await get_result(session, result_id)
    except SQLAlchemyError as error:
        logger.error("evaluation_result_read_failed")
        raise _persistence_error() from error
    if result is None:
        raise _not_found("EVALUATION_RESULT_NOT_FOUND", "evaluation result was not found")
    return result
