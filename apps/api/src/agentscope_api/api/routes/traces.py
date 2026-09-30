from __future__ import annotations

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, Query
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agentscope_api.database import DatabaseSession
from agentscope_api.schemas.trace_queries import (
    CursorFilterMismatchError,
    InvalidCursorError,
    TraceDetail,
    TraceListParams,
    TraceListResponse,
)
from agentscope_api.schemas.traces import IngestionEnvelopeV1, IngestionResponse
from agentscope_api.services.trace_ingestion import DuplicateTraceConflict, ingest_traces
from agentscope_api.services.trace_query import get_trace, list_traces

router = APIRouter(prefix="/api/v1/traces", tags=["traces"])
logger = logging.getLogger(__name__)

IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=255, pattern=r"^[!-~]+$"),
]
TraceId = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[^\s]+$")]


@router.get("", response_model=TraceListResponse)
async def read_traces(
    session: DatabaseSession,
    params: Annotated[TraceListParams, Query()],
) -> TraceListResponse:
    started = time.perf_counter()
    try:
        result = await list_traces(session, params)
    except CursorFilterMismatchError as error:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CURSOR_FILTER_MISMATCH",
                "message": "cursor does not match query filters",
            },
        ) from error
    except InvalidCursorError as error:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_CURSOR", "message": "invalid cursor"},
        ) from error
    except SQLAlchemyError as error:
        logger.error("trace_query_failed duration_ms=%.3f", (time.perf_counter() - started) * 1_000)
        raise HTTPException(
            status_code=500,
            detail={"code": "QUERY_ERROR", "message": "trace query failed"},
        ) from error
    logger.info(
        "trace_query_succeeded page_size=%d result_count=%d duration_ms=%.3f",
        params.page_size,
        len(result.items),
        (time.perf_counter() - started) * 1_000,
    )
    return result


@router.get("/{trace_id}", response_model=TraceDetail)
async def read_trace(trace_id: TraceId, session: DatabaseSession) -> TraceDetail:
    started = time.perf_counter()
    try:
        result = await get_trace(session, trace_id)
    except SQLAlchemyError as error:
        logger.error(
            "trace_detail_failed duration_ms=%.3f",
            (time.perf_counter() - started) * 1_000,
        )
        raise HTTPException(
            status_code=500,
            detail={"code": "QUERY_ERROR", "message": "trace query failed"},
        ) from error
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "TRACE_NOT_FOUND", "message": "trace was not found"},
        )
    return result


@router.post("", response_model=IngestionResponse, status_code=202)
async def create_traces(
    envelope: IngestionEnvelopeV1,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey = None,
) -> IngestionResponse:
    started = time.perf_counter()
    try:
        result = await ingest_traces(session, envelope)
    except DuplicateTraceConflict as error:
        logger.info(
            "trace_ingestion_conflict trace_count=%d duration_ms=%.3f",
            len(envelope.traces),
            (time.perf_counter() - started) * 1_000,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TRACE_CONFLICT",
                "message": "trace_id already exists with a different payload",
                "trace_ids": error.trace_ids,
            },
        ) from error
    except IntegrityError as error:
        logger.info(
            "trace_ingestion_integrity_conflict trace_count=%d duration_ms=%.3f",
            len(envelope.traces),
            (time.perf_counter() - started) * 1_000,
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "TRACE_CONFLICT", "message": "trace identifiers conflict"},
        ) from error
    except SQLAlchemyError as error:
        logger.error(
            "trace_ingestion_failed trace_count=%d duration_ms=%.3f",
            len(envelope.traces),
            (time.perf_counter() - started) * 1_000,
        )
        raise HTTPException(
            status_code=500,
            detail={"code": "PERSISTENCE_ERROR", "message": "trace persistence failed"},
        ) from error

    logger.info(
        "trace_ingestion_accepted trace_count=%d accepted=%d duplicates=%d duration_ms=%.3f",
        len(envelope.traces),
        result.accepted,
        result.duplicates,
        (time.perf_counter() - started) * 1_000,
    )
    return IngestionResponse(accepted=result.accepted, duplicates=result.duplicates)
