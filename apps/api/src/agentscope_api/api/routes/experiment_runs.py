from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agentscope_api.database import DatabaseSession
from agentscope_api.jobs.experiments import enqueue_experiment_run
from agentscope_api.schemas.experiment_analysis import (
    ExperimentChangedSubjectList,
    ExperimentChangeListParams,
    ExperimentRunAnalysis,
)
from agentscope_api.schemas.experiment_runs import (
    ExperimentRun,
    ExperimentRunExecutionAccepted,
    ExperimentRunList,
    ExperimentRunListParams,
    ExperimentRunResultList,
    ExperimentRunResultListParams,
)
from agentscope_api.services.experiment_analysis import (
    ExperimentAnalysisNotFound,
    ExperimentConditionNotAnalyzable,
    ExperimentConditionNotFound,
    ExperimentRunNotCompleted,
    IncompleteExperimentPopulation,
    create_experiment_analysis,
    get_experiment_analysis,
    list_changed_subjects,
)
from agentscope_api.services.experiment_execution import (
    ExperimentRunNotExecutable,
    mark_experiment_run_enqueued,
    submit_experiment_run,
)
from agentscope_api.services.experiment_runs import (
    ExperimentNotExecutable,
    ExperimentRunConflict,
    ExperimentRunNotFound,
    create_experiment_run,
    get_experiment_run,
    list_experiment_results,
    list_experiment_runs,
)

router = APIRouter(prefix="/api/v1", tags=["experiment-runs"])
logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _persistence_error() -> HTTPException:
    return _error(500, "PERSISTENCE_ERROR", "experiment run persistence failed")


@router.post("/experiments/{experiment_id}/runs", response_model=ExperimentRun, status_code=201)
async def create_run(experiment_id: UUID, session: DatabaseSession) -> ExperimentRun:
    try:
        return await create_experiment_run(session, experiment_id)
    except ExperimentRunNotFound as error:
        raise _error(404, "EXPERIMENT_NOT_FOUND", "experiment was not found") from error
    except ExperimentNotExecutable as error:
        raise _error(409, "EXPERIMENT_NOT_EXECUTABLE", "experiment is not executable") from error
    except (ExperimentRunConflict, IntegrityError) as error:
        raise _error(
            409, "EXPERIMENT_RUN_CONFLICT", "experiment already has an active run"
        ) from error
    except SQLAlchemyError as error:
        logger.error("experiment_run_create_failed experiment_id=%s", experiment_id)
        raise _persistence_error() from error


@router.get("/experiments/{experiment_id}/runs", response_model=ExperimentRunList)
async def read_runs(
    experiment_id: UUID,
    session: DatabaseSession,
    params: Annotated[ExperimentRunListParams, Query()],
) -> ExperimentRunList:
    try:
        return await list_experiment_runs(session, experiment_id, params)
    except ExperimentRunNotFound as error:
        raise _error(404, "EXPERIMENT_NOT_FOUND", "experiment was not found") from error
    except SQLAlchemyError as error:
        logger.error("experiment_run_list_failed experiment_id=%s", experiment_id)
        raise _persistence_error() from error


@router.get("/experiment-runs/{run_id}", response_model=ExperimentRun)
async def read_run(run_id: UUID, session: DatabaseSession) -> ExperimentRun:
    try:
        run = await get_experiment_run(session, run_id)
    except SQLAlchemyError as error:
        logger.error("experiment_run_read_failed run_id=%s", run_id)
        raise _persistence_error() from error
    if run is None:
        raise _error(404, "EXPERIMENT_RUN_NOT_FOUND", "experiment run was not found")
    return run


@router.post(
    "/experiment-runs/{run_id}/execute",
    response_model=ExperimentRunExecutionAccepted,
    status_code=202,
)
async def execute_run(
    run_id: UUID, session: DatabaseSession
) -> ExperimentRunExecutionAccepted:
    try:
        await submit_experiment_run(session, run_id)
    except ExperimentRunNotFound as error:
        raise _error(404, "EXPERIMENT_RUN_NOT_FOUND", "experiment run was not found") from error
    except ExperimentRunNotExecutable as error:
        raise _error(
            409, "EXPERIMENT_RUN_NOT_EXECUTABLE", "experiment run is not executable"
        ) from error
    except SQLAlchemyError as error:
        logger.error("experiment_run_submit_failed run_id=%s", run_id)
        raise _persistence_error() from error

    delivery: Literal["enqueued", "deferred"] = "enqueued"
    try:
        await asyncio.to_thread(enqueue_experiment_run, run_id)
    except Exception:
        delivery = "deferred"
        logger.warning("experiment_run_enqueue_deferred run_id=%s", run_id)
    else:
        try:
            await mark_experiment_run_enqueued(session, run_id)
        except SQLAlchemyError:
            logger.warning("experiment_run_enqueue_marker_failed run_id=%s", run_id)
    return ExperimentRunExecutionAccepted(run_id=run_id, queue_delivery=delivery)


@router.get("/experiment-runs/{run_id}/results", response_model=ExperimentRunResultList)
async def read_results(
    run_id: UUID,
    session: DatabaseSession,
    params: Annotated[ExperimentRunResultListParams, Query()],
) -> ExperimentRunResultList:
    try:
        return await list_experiment_results(session, run_id, params)
    except ExperimentRunNotFound as error:
        raise _error(404, "EXPERIMENT_RUN_NOT_FOUND", "experiment run was not found") from error
    except SQLAlchemyError as error:
        logger.error("experiment_result_list_failed run_id=%s", run_id)
        raise _persistence_error() from error


@router.post(
    "/experiment-runs/{run_id}/analysis",
    response_model=ExperimentRunAnalysis,
    status_code=201,
)
async def create_analysis(run_id: UUID, session: DatabaseSession) -> ExperimentRunAnalysis:
    try:
        return await create_experiment_analysis(session, run_id)
    except ExperimentRunNotFound as error:
        raise _error(404, "EXPERIMENT_RUN_NOT_FOUND", "experiment run was not found") from error
    except ExperimentRunNotCompleted as error:
        raise _error(
            409, "EXPERIMENT_RUN_NOT_COMPLETED", "experiment run is not completed"
        ) from error
    except IncompleteExperimentPopulation as error:
        raise _error(
            409,
            "INCOMPLETE_EXPERIMENT_POPULATION",
            "experiment results do not exactly match the frozen population",
        ) from error
    except SQLAlchemyError as error:
        logger.error("experiment_analysis_create_failed run_id=%s", run_id)
        raise _persistence_error() from error


@router.get(
    "/experiment-runs/{run_id}/analysis",
    response_model=ExperimentRunAnalysis,
)
async def read_analysis(run_id: UUID, session: DatabaseSession) -> ExperimentRunAnalysis:
    try:
        return await get_experiment_analysis(session, run_id)
    except ExperimentRunNotFound as error:
        raise _error(404, "EXPERIMENT_RUN_NOT_FOUND", "experiment run was not found") from error
    except ExperimentAnalysisNotFound as error:
        raise _error(
            404, "EXPERIMENT_ANALYSIS_NOT_FOUND", "experiment analysis was not found"
        ) from error
    except SQLAlchemyError as error:
        logger.error("experiment_analysis_read_failed run_id=%s", run_id)
        raise _persistence_error() from error


@router.get(
    "/experiment-runs/{run_id}/analysis/changes",
    response_model=ExperimentChangedSubjectList,
)
async def read_analysis_changes(
    run_id: UUID,
    session: DatabaseSession,
    params: Annotated[ExperimentChangeListParams, Query()],
) -> ExperimentChangedSubjectList:
    try:
        return await list_changed_subjects(session, run_id, params)
    except ExperimentRunNotFound as error:
        raise _error(404, "EXPERIMENT_RUN_NOT_FOUND", "experiment run was not found") from error
    except ExperimentAnalysisNotFound as error:
        raise _error(
            404, "EXPERIMENT_ANALYSIS_NOT_FOUND", "experiment analysis was not found"
        ) from error
    except ExperimentConditionNotFound as error:
        raise _error(
            404, "EXPERIMENT_CONDITION_NOT_FOUND", "experiment condition was not found"
        ) from error
    except ExperimentConditionNotAnalyzable as error:
        raise _error(
            409,
            "EXPERIMENT_CONDITION_NOT_ANALYZABLE",
            "experiment condition has non-binary outcomes",
        ) from error
    except SQLAlchemyError as error:
        logger.error("experiment_analysis_changes_failed run_id=%s", run_id)
        raise _persistence_error() from error
