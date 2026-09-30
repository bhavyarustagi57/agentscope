from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agentscope_api.database import DatabaseSession
from agentscope_api.schemas.experiments import (
    Experiment,
    ExperimentConfigure,
    ExperimentCreate,
    ExperimentList,
    ExperimentListParams,
)
from agentscope_api.services.experiments import (
    ExperimentEvaluationDefinitionDisabled,
    ExperimentEvaluationDefinitionNotFound,
    ExperimentImmutable,
    ExperimentIncomplete,
    ExperimentNotFound,
    ExperimentTraceNotFound,
    configure_experiment,
    create_experiment,
    get_experiment,
    list_experiments,
    mark_experiment_ready,
)

router = APIRouter(prefix="/api/v1/experiments", tags=["experiments"])
logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _persistence_error() -> HTTPException:
    return _error(500, "PERSISTENCE_ERROR", "experiment persistence failed")


@router.post("", response_model=Experiment, status_code=201)
async def create(payload: ExperimentCreate, session: DatabaseSession) -> Experiment:
    try:
        return await create_experiment(session, payload)
    except SQLAlchemyError as error:
        logger.error("experiment_create_failed")
        raise _persistence_error() from error


@router.get("", response_model=ExperimentList)
async def read_many(
    session: DatabaseSession,
    params: Annotated[ExperimentListParams, Query()],
) -> ExperimentList:
    try:
        return await list_experiments(session, params)
    except SQLAlchemyError as error:
        logger.error("experiment_list_failed")
        raise _persistence_error() from error


@router.get("/{experiment_id}", response_model=Experiment)
async def read_one(experiment_id: UUID, session: DatabaseSession) -> Experiment:
    try:
        experiment = await get_experiment(session, experiment_id)
    except SQLAlchemyError as error:
        logger.error("experiment_read_failed experiment_id=%s", experiment_id)
        raise _persistence_error() from error
    if experiment is None:
        raise _error(404, "EXPERIMENT_NOT_FOUND", "experiment was not found")
    return experiment


@router.post("/{experiment_id}/configuration", response_model=Experiment)
async def configure(
    experiment_id: UUID, payload: ExperimentConfigure, session: DatabaseSession
) -> Experiment:
    try:
        return await configure_experiment(session, experiment_id, payload)
    except ExperimentNotFound as error:
        raise _error(404, "EXPERIMENT_NOT_FOUND", "experiment was not found") from error
    except ExperimentTraceNotFound as error:
        raise _error(404, "TRACE_NOT_FOUND", "one or more traces were not found") from error
    except ExperimentEvaluationDefinitionNotFound as error:
        raise _error(
            404,
            "EVALUATION_DEFINITION_NOT_FOUND",
            "one or more evaluation definitions were not found",
        ) from error
    except ExperimentEvaluationDefinitionDisabled as error:
        raise _error(
            409,
            "EVALUATION_DEFINITION_DISABLED",
            "experiment evaluation definitions must be enabled",
        ) from error
    except ExperimentImmutable as error:
        raise _error(
            409,
            "EXPERIMENT_IMMUTABLE",
            "experiment configuration is immutable after draft",
        ) from error
    except IntegrityError as error:
        raise _error(409, "EXPERIMENT_CONFLICT", "experiment configuration conflicts") from error
    except SQLAlchemyError as error:
        logger.error("experiment_configure_failed experiment_id=%s", experiment_id)
        raise _persistence_error() from error


@router.post("/{experiment_id}/ready", response_model=Experiment)
async def ready(experiment_id: UUID, session: DatabaseSession) -> Experiment:
    try:
        return await mark_experiment_ready(session, experiment_id)
    except ExperimentNotFound as error:
        raise _error(404, "EXPERIMENT_NOT_FOUND", "experiment was not found") from error
    except ExperimentIncomplete as error:
        raise _error(
            409,
            "EXPERIMENT_INCOMPLETE",
            "experiment requires A/B variants, paired subjects, and evaluation conditions",
        ) from error
    except ExperimentImmutable as error:
        raise _error(
            409, "ILLEGAL_EXPERIMENT_TRANSITION", "experiment cannot become ready"
        ) from error
    except SQLAlchemyError as error:
        logger.error("experiment_ready_failed experiment_id=%s", experiment_id)
        raise _persistence_error() from error
