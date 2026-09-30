from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agentscope_api.database import DatabaseSession
from agentscope_api.jobs.bisection_analysis import enqueue_bisection_analysis
from agentscope_api.schemas.bisection_analysis import (
    BisectionAnalysis,
    BisectionAnalysisAccepted,
    BisectionAnalysisCreate,
    BisectionAnalysisList,
    BisectionAnalysisListParams,
)
from agentscope_api.services.bisection_analysis import (
    BisectionAnalysisConflict,
    BisectionAnalysisNotFound,
    BisectionAnalysisNotPending,
    BisectionSessionNotFound,
    BisectionSessionNotReady,
    create_analysis,
    get_analysis,
    list_analyses,
    mark_analysis_enqueued,
    start_analysis,
)

router = APIRouter(prefix="/api/v1", tags=["bisection-analysis"])
logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


@router.post(
    "/bisection-sessions/{session_id}/analyses",
    response_model=BisectionAnalysis,
    status_code=201,
)
async def create_bisection_analysis(
    session_id: UUID, payload: BisectionAnalysisCreate, session: DatabaseSession
) -> BisectionAnalysis:
    try:
        return await create_analysis(session, session_id, payload)
    except BisectionSessionNotFound as error:
        raise _error(
            404, "BISECTION_SESSION_NOT_FOUND", "bisection session was not found"
        ) from error
    except BisectionSessionNotReady as error:
        raise _error(
            409, "BISECTION_SESSION_NOT_READY", "bisection session is not ready"
        ) from error
    except (BisectionAnalysisConflict, IntegrityError) as error:
        raise _error(
            409, "BISECTION_ANALYSIS_CONFLICT", "session already has an active analysis"
        ) from error
    except SQLAlchemyError as error:
        logger.error("bisection_analysis_create_failed session_id=%s", session_id)
        raise _error(500, "PERSISTENCE_ERROR", "bisection analysis persistence failed") from error


@router.get("/bisection-sessions/{session_id}/analyses", response_model=BisectionAnalysisList)
async def read_bisection_analyses(
    session_id: UUID,
    session: DatabaseSession,
    params: Annotated[BisectionAnalysisListParams, Query()],
) -> BisectionAnalysisList:
    try:
        return await list_analyses(session, session_id, params)
    except BisectionSessionNotFound as error:
        raise _error(
            404, "BISECTION_SESSION_NOT_FOUND", "bisection session was not found"
        ) from error
    except SQLAlchemyError as error:
        logger.error("bisection_analysis_list_failed session_id=%s", session_id)
        raise _error(500, "PERSISTENCE_ERROR", "bisection analysis persistence failed") from error


@router.get("/bisection-analyses/{analysis_id}", response_model=BisectionAnalysis)
async def read_bisection_analysis(analysis_id: UUID, session: DatabaseSession) -> BisectionAnalysis:
    try:
        result = await get_analysis(session, analysis_id)
    except SQLAlchemyError as error:
        logger.error("bisection_analysis_read_failed analysis_id=%s", analysis_id)
        raise _error(500, "PERSISTENCE_ERROR", "bisection analysis persistence failed") from error
    if result is None:
        raise _error(404, "BISECTION_ANALYSIS_NOT_FOUND", "bisection analysis was not found")
    return result


@router.post(
    "/bisection-analyses/{analysis_id}/execute",
    response_model=BisectionAnalysisAccepted,
    status_code=202,
)
async def execute_bisection_analysis(
    analysis_id: UUID, session: DatabaseSession
) -> BisectionAnalysisAccepted:
    try:
        await start_analysis(session, analysis_id)
    except BisectionAnalysisNotFound as error:
        raise _error(
            404, "BISECTION_ANALYSIS_NOT_FOUND", "bisection analysis was not found"
        ) from error
    except BisectionAnalysisNotPending as error:
        raise _error(
            409, "BISECTION_ANALYSIS_NOT_PENDING", "bisection analysis is not pending"
        ) from error
    except SQLAlchemyError as error:
        logger.error("bisection_analysis_start_failed analysis_id=%s", analysis_id)
        raise _error(500, "PERSISTENCE_ERROR", "bisection analysis persistence failed") from error

    delivery: Literal["enqueued", "deferred"] = "enqueued"
    try:
        await asyncio.to_thread(enqueue_bisection_analysis, analysis_id)
    except Exception:
        delivery = "deferred"
        logger.warning("bisection_analysis_enqueue_deferred analysis_id=%s", analysis_id)
    else:
        try:
            await mark_analysis_enqueued(session, analysis_id)
        except SQLAlchemyError:
            logger.warning("bisection_analysis_enqueue_marker_failed analysis_id=%s", analysis_id)
    return BisectionAnalysisAccepted(analysis_id=analysis_id, queue_delivery=delivery)
