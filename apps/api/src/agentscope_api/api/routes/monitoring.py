from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from agentscope_api.database import DatabaseSession
from agentscope_api.jobs.monitoring import enqueue_monitoring_snapshot
from agentscope_api.schemas.monitoring import (
    MonitoringDefinition,
    MonitoringDefinitionCreate,
    MonitoringDefinitionEnabledUpdate,
    MonitoringDefinitionList,
    MonitoringDefinitionListParams,
    MonitoringMaterializationRequest,
    MonitoringSnapshot,
    MonitoringSnapshotAccepted,
    MonitoringSnapshotList,
    MonitoringSnapshotListParams,
)
from agentscope_api.services.monitoring import (
    EvaluationDefinitionNotFound,
    InvalidMonitoringWindow,
    MonitoringDefinitionNotFound,
    create_definition,
    create_snapshot_intent,
    get_definition,
    get_snapshot,
    list_definitions,
    list_snapshots,
    mark_snapshot_enqueued,
    set_definition_enabled,
)

router = APIRouter(prefix="/api/v1", tags=["monitoring"])
logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


@router.post("/monitoring-definitions", response_model=MonitoringDefinition, status_code=201)
async def create_monitoring_definition(
    payload: MonitoringDefinitionCreate, session: DatabaseSession
) -> MonitoringDefinition:
    try:
        return await create_definition(session, payload)
    except EvaluationDefinitionNotFound as error:
        raise _error(
            404, "EVALUATION_DEFINITION_NOT_FOUND", "evaluation definition was not found"
        ) from error
    except SQLAlchemyError as error:
        logger.error("monitoring_definition_create_failed")
        raise _error(
            500, "PERSISTENCE_ERROR", "monitoring definition persistence failed"
        ) from error


@router.get("/monitoring-definitions", response_model=MonitoringDefinitionList)
async def read_monitoring_definitions(
    session: DatabaseSession,
    params: Annotated[MonitoringDefinitionListParams, Query()],
) -> MonitoringDefinitionList:
    return await list_definitions(session, params)


@router.get("/monitoring-definitions/{definition_id}", response_model=MonitoringDefinition)
async def read_monitoring_definition(
    definition_id: UUID, session: DatabaseSession
) -> MonitoringDefinition:
    result = await get_definition(session, definition_id)
    if result is None:
        raise _error(404, "MONITORING_DEFINITION_NOT_FOUND", "monitoring definition was not found")
    return result


@router.patch("/monitoring-definitions/{definition_id}", response_model=MonitoringDefinition)
async def update_monitoring_definition(
    definition_id: UUID,
    payload: MonitoringDefinitionEnabledUpdate,
    session: DatabaseSession,
) -> MonitoringDefinition:
    try:
        return await set_definition_enabled(session, definition_id, payload.is_enabled)
    except MonitoringDefinitionNotFound as error:
        raise _error(
            404, "MONITORING_DEFINITION_NOT_FOUND", "monitoring definition was not found"
        ) from error


@router.post(
    "/monitoring-definitions/{definition_id}/snapshots",
    response_model=MonitoringSnapshotAccepted,
    status_code=202,
)
async def materialize_monitoring_snapshot(
    definition_id: UUID,
    payload: MonitoringMaterializationRequest,
    session: DatabaseSession,
) -> MonitoringSnapshotAccepted:
    try:
        snapshot, created = await create_snapshot_intent(
            session,
            definition_id,
            window_start=payload.window_start,
            window_end=payload.window_end,
        )
    except MonitoringDefinitionNotFound as error:
        raise _error(
            404, "MONITORING_DEFINITION_NOT_FOUND", "monitoring definition was not found"
        ) from error
    except InvalidMonitoringWindow as error:
        raise _error(422, "INVALID_MONITORING_WINDOW", str(error)) from error

    delivery: Literal["enqueued", "deferred", "not_required"] = "not_required"
    if created:
        delivery = "enqueued"
        try:
            await asyncio.to_thread(enqueue_monitoring_snapshot, snapshot.id)
        except Exception:
            delivery = "deferred"
            logger.warning("monitoring_snapshot_enqueue_deferred snapshot_id=%s", snapshot.id)
        else:
            try:
                await mark_snapshot_enqueued(session, snapshot.id)
            except SQLAlchemyError:
                logger.warning(
                    "monitoring_snapshot_enqueue_marker_failed snapshot_id=%s", snapshot.id
                )
    return MonitoringSnapshotAccepted(
        snapshot_id=snapshot.id,
        status=snapshot.status,
        queue_delivery=delivery,
        created=created,
    )


@router.get(
    "/monitoring-definitions/{definition_id}/snapshots", response_model=MonitoringSnapshotList
)
async def read_monitoring_snapshots(
    definition_id: UUID,
    session: DatabaseSession,
    params: Annotated[MonitoringSnapshotListParams, Query()],
) -> MonitoringSnapshotList:
    try:
        return await list_snapshots(session, definition_id, params)
    except MonitoringDefinitionNotFound as error:
        raise _error(
            404, "MONITORING_DEFINITION_NOT_FOUND", "monitoring definition was not found"
        ) from error


@router.get("/monitoring-snapshots/{snapshot_id}", response_model=MonitoringSnapshot)
async def read_monitoring_snapshot(
    snapshot_id: UUID, session: DatabaseSession
) -> MonitoringSnapshot:
    result = await get_snapshot(session, snapshot_id)
    if result is None:
        raise _error(404, "MONITORING_SNAPSHOT_NOT_FOUND", "monitoring snapshot was not found")
    return result
