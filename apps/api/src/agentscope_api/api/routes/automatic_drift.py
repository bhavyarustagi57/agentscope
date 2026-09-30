from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from agentscope_api.database import DatabaseSession
from agentscope_api.schemas.automatic_drift import (
    AutomaticDriftCheck,
    AutomaticDriftCheckList,
    AutomaticDriftCheckListParams,
    AutomaticDriftConfiguration,
    AutomaticDriftConfigurationCreate,
    AutomaticDriftConfigurationList,
    AutomaticDriftConfigurationListParams,
    AutomaticDriftConfigurationUpdate,
    MonitoringIncident,
    MonitoringIncidentEventList,
    MonitoringIncidentEventListParams,
    MonitoringIncidentList,
    MonitoringIncidentListParams,
)
from agentscope_api.services.automatic_drift import (
    AutomaticDriftConfigurationNotFound,
    DriftPolicyNotFound,
    MonitoringDefinitionNotFound,
    MonitoringIncidentAlreadyResolved,
    MonitoringIncidentNotFound,
    acknowledge_incident,
    create_configuration,
    get_check,
    get_configuration,
    get_incident,
    list_checks,
    list_configurations,
    list_incident_events,
    list_incidents,
    update_configuration,
)

configuration_router = APIRouter(
    prefix="/api/v1/automatic-drift-configurations", tags=["automatic-drift-configurations"]
)
check_router = APIRouter(prefix="/api/v1/automatic-drift-checks", tags=["automatic-drift-checks"])
incident_router = APIRouter(prefix="/api/v1/monitoring-incidents", tags=["monitoring-incidents"])
logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _persistence_error() -> HTTPException:
    return _error(500, "PERSISTENCE_ERROR", "automatic drift persistence failed")


@configuration_router.post("", response_model=AutomaticDriftConfiguration, status_code=201)
async def create_automatic_configuration(
    payload: AutomaticDriftConfigurationCreate, session: DatabaseSession
) -> AutomaticDriftConfiguration:
    try:
        return await create_configuration(session, payload)
    except MonitoringDefinitionNotFound as error:
        raise _error(
            404, "MONITORING_DEFINITION_NOT_FOUND", "monitoring definition was not found"
        ) from error
    except DriftPolicyNotFound as error:
        raise _error(404, "DRIFT_POLICY_NOT_FOUND", "drift policy was not found") from error
    except SQLAlchemyError as error:
        logger.error("automatic_drift_configuration_create_failed")
        raise _persistence_error() from error


@configuration_router.get("", response_model=AutomaticDriftConfigurationList)
async def read_automatic_configurations(
    session: DatabaseSession,
    params: Annotated[AutomaticDriftConfigurationListParams, Query()],
) -> AutomaticDriftConfigurationList:
    try:
        return await list_configurations(session, params)
    except SQLAlchemyError as error:
        logger.error("automatic_drift_configuration_list_failed")
        raise _persistence_error() from error


@configuration_router.get("/{configuration_id}", response_model=AutomaticDriftConfiguration)
async def read_automatic_configuration(
    configuration_id: UUID, session: DatabaseSession
) -> AutomaticDriftConfiguration:
    result = await get_configuration(session, configuration_id)
    if result is None:
        raise _error(404, "AUTOMATIC_DRIFT_CONFIGURATION_NOT_FOUND", "configuration was not found")
    return result


@configuration_router.patch("/{configuration_id}", response_model=AutomaticDriftConfiguration)
async def toggle_automatic_configuration(
    configuration_id: UUID,
    payload: AutomaticDriftConfigurationUpdate,
    session: DatabaseSession,
) -> AutomaticDriftConfiguration:
    try:
        return await update_configuration(session, configuration_id, payload)
    except AutomaticDriftConfigurationNotFound as error:
        raise _error(
            404, "AUTOMATIC_DRIFT_CONFIGURATION_NOT_FOUND", "configuration was not found"
        ) from error
    except SQLAlchemyError as error:
        logger.error(
            "automatic_drift_configuration_update_failed configuration_id=%s", configuration_id
        )
        raise _persistence_error() from error


@check_router.get("", response_model=AutomaticDriftCheckList)
async def read_automatic_checks(
    session: DatabaseSession, params: Annotated[AutomaticDriftCheckListParams, Query()]
) -> AutomaticDriftCheckList:
    return await list_checks(session, params)


@check_router.get("/{check_id}", response_model=AutomaticDriftCheck)
async def read_automatic_check(check_id: UUID, session: DatabaseSession) -> AutomaticDriftCheck:
    result = await get_check(session, check_id)
    if result is None:
        raise _error(404, "AUTOMATIC_DRIFT_CHECK_NOT_FOUND", "automatic drift check was not found")
    return result


@incident_router.get("", response_model=MonitoringIncidentList)
async def read_incidents(
    session: DatabaseSession, params: Annotated[MonitoringIncidentListParams, Query()]
) -> MonitoringIncidentList:
    return await list_incidents(session, params)


@incident_router.get("/{incident_id}", response_model=MonitoringIncident)
async def read_incident(incident_id: UUID, session: DatabaseSession) -> MonitoringIncident:
    result = await get_incident(session, incident_id)
    if result is None:
        raise _error(404, "MONITORING_INCIDENT_NOT_FOUND", "monitoring incident was not found")
    return result


@incident_router.post("/{incident_id}/acknowledge", response_model=MonitoringIncident)
async def acknowledge(incident_id: UUID, session: DatabaseSession) -> MonitoringIncident:
    try:
        return await acknowledge_incident(session, incident_id)
    except MonitoringIncidentNotFound as error:
        raise _error(
            404, "MONITORING_INCIDENT_NOT_FOUND", "monitoring incident was not found"
        ) from error
    except MonitoringIncidentAlreadyResolved as error:
        raise _error(
            409, "MONITORING_INCIDENT_RESOLVED", "resolved incidents cannot be acknowledged"
        ) from error


@incident_router.get("/{incident_id}/events", response_model=MonitoringIncidentEventList)
async def read_incident_events(
    incident_id: UUID,
    session: DatabaseSession,
    params: Annotated[MonitoringIncidentEventListParams, Query()],
) -> MonitoringIncidentEventList:
    try:
        return await list_incident_events(session, incident_id, params)
    except MonitoringIncidentNotFound as error:
        raise _error(
            404, "MONITORING_INCIDENT_NOT_FOUND", "monitoring incident was not found"
        ) from error
