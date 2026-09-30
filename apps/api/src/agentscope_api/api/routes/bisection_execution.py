from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agentscope_api.database import DatabaseSession
from agentscope_api.jobs.bisection_execution import enqueue_bisection_execution
from agentscope_api.schemas.bisection_execution import (
    BisectionExecutionAccepted,
    BisectionExecutionRun,
    BisectionExecutionRunCreate,
    BisectionExecutionRunList,
    BisectionExecutionRunListParams,
    BisectionExecutionRunSubmit,
)
from agentscope_api.services.bisection_execution import (
    BisectionExecutionRunConflict,
    BisectionExecutionRunNotFound,
    BisectionExecutionRunNotPending,
    BisectionSessionNotFound,
    BisectionSessionNotReady,
    CommitNotInBisectionPlan,
    create_execution_run,
    get_execution_run,
    list_execution_runs,
    mark_execution_run_enqueued,
    submit_execution_run,
)

router = APIRouter(prefix="/api/v1", tags=["bisection-execution"])
logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


@router.post(
    "/bisection-sessions/{session_id}/execution-runs",
    response_model=BisectionExecutionRun,
    status_code=201,
)
async def create_run(
    session_id: UUID,
    payload: BisectionExecutionRunCreate,
    session: DatabaseSession,
) -> BisectionExecutionRun:
    try:
        return await create_execution_run(session, session_id, payload)
    except BisectionSessionNotFound as error:
        raise _error(
            404, "BISECTION_SESSION_NOT_FOUND", "bisection session was not found"
        ) from error
    except BisectionSessionNotReady as error:
        raise _error(
            409, "BISECTION_SESSION_NOT_READY", "bisection session is not ready"
        ) from error
    except (BisectionExecutionRunConflict, IntegrityError) as error:
        raise _error(
            409, "BISECTION_EXECUTION_RUN_CONFLICT", "session already has an active execution run"
        ) from error
    except SQLAlchemyError as error:
        logger.error("bisection_execution_run_create_failed session_id=%s", session_id)
        raise _error(500, "PERSISTENCE_ERROR", "bisection execution persistence failed") from error


@router.get(
    "/bisection-sessions/{session_id}/execution-runs",
    response_model=BisectionExecutionRunList,
)
async def read_runs(
    session_id: UUID,
    session: DatabaseSession,
    params: Annotated[BisectionExecutionRunListParams, Query()],
) -> BisectionExecutionRunList:
    try:
        return await list_execution_runs(session, session_id, params)
    except BisectionSessionNotFound as error:
        raise _error(
            404, "BISECTION_SESSION_NOT_FOUND", "bisection session was not found"
        ) from error
    except SQLAlchemyError as error:
        logger.error("bisection_execution_run_list_failed session_id=%s", session_id)
        raise _error(500, "PERSISTENCE_ERROR", "bisection execution persistence failed") from error


@router.get("/bisection-execution-runs/{run_id}", response_model=BisectionExecutionRun)
async def read_run(run_id: UUID, session: DatabaseSession) -> BisectionExecutionRun:
    try:
        result = await get_execution_run(session, run_id)
    except SQLAlchemyError as error:
        logger.error("bisection_execution_run_read_failed run_id=%s", run_id)
        raise _error(500, "PERSISTENCE_ERROR", "bisection execution persistence failed") from error
    if result is None:
        raise _error(404, "BISECTION_EXECUTION_RUN_NOT_FOUND", "execution run was not found")
    return result


@router.post(
    "/bisection-execution-runs/{run_id}/execute",
    response_model=BisectionExecutionAccepted,
    status_code=202,
)
async def execute_run(
    run_id: UUID,
    payload: BisectionExecutionRunSubmit,
    session: DatabaseSession,
) -> BisectionExecutionAccepted:
    try:
        await submit_execution_run(session, run_id, payload)
    except BisectionExecutionRunNotFound as error:
        raise _error(
            404, "BISECTION_EXECUTION_RUN_NOT_FOUND", "execution run was not found"
        ) from error
    except BisectionExecutionRunNotPending as error:
        raise _error(
            409, "BISECTION_EXECUTION_RUN_NOT_PENDING", "execution run is not pending"
        ) from error
    except CommitNotInBisectionPlan as error:
        raise _error(
            422, "COMMIT_NOT_IN_BISECTION_PLAN", "requested commit is not in the frozen plan"
        ) from error
    except SQLAlchemyError as error:
        logger.error("bisection_execution_run_submit_failed run_id=%s", run_id)
        raise _error(500, "PERSISTENCE_ERROR", "bisection execution persistence failed") from error

    delivery: Literal["enqueued", "deferred"] = "enqueued"
    try:
        await asyncio.to_thread(enqueue_bisection_execution, run_id)
    except Exception:
        delivery = "deferred"
        logger.warning("bisection_execution_enqueue_deferred run_id=%s", run_id)
    else:
        try:
            await mark_execution_run_enqueued(session, run_id)
        except SQLAlchemyError:
            logger.warning("bisection_execution_enqueue_marker_failed run_id=%s", run_id)
    return BisectionExecutionAccepted(run_id=run_id, queue_delivery=delivery)
