from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from agentscope_api.database import DatabaseSession
from agentscope_api.schemas.drift import (
    DriftComparison,
    DriftComparisonCreate,
    DriftComparisonList,
    DriftComparisonListParams,
    DriftPolicy,
    DriftPolicyCreate,
    DriftPolicyList,
    DriftPolicyListParams,
)
from agentscope_api.services.drift import (
    DriftBaselineNotBeforeCurrent,
    DriftMonitorMismatch,
    DriftPolicyEmpty,
    DriftPolicyNotFound,
    DriftSnapshotNotCompleted,
    DriftSnapshotsMustDiffer,
    DriftWindowDurationMismatch,
    DriftWindowsOverlap,
    MonitoringSnapshotNotFound,
    create_drift_comparison,
    create_drift_policy,
    get_drift_comparison,
    get_drift_policy,
    list_drift_comparisons,
    list_drift_policies,
)

policy_router = APIRouter(prefix="/api/v1/drift-policies", tags=["drift-policies"])
comparison_router = APIRouter(prefix="/api/v1/drift-comparisons", tags=["drift-comparisons"])
logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _persistence_error() -> HTTPException:
    return _error(500, "PERSISTENCE_ERROR", "drift persistence failed")


@policy_router.post("", response_model=DriftPolicy, status_code=201)
async def create_policy(payload: DriftPolicyCreate, session: DatabaseSession) -> DriftPolicy:
    try:
        return await create_drift_policy(session, payload)
    except SQLAlchemyError as error:
        logger.error("drift_policy_create_failed")
        raise _persistence_error() from error


@policy_router.get("", response_model=DriftPolicyList)
async def read_policies(
    session: DatabaseSession,
    params: Annotated[DriftPolicyListParams, Query()],
) -> DriftPolicyList:
    try:
        return await list_drift_policies(session, params)
    except SQLAlchemyError as error:
        logger.error("drift_policy_list_failed")
        raise _persistence_error() from error


@policy_router.get("/{policy_id}", response_model=DriftPolicy)
async def read_policy(policy_id: UUID, session: DatabaseSession) -> DriftPolicy:
    try:
        result = await get_drift_policy(session, policy_id)
    except SQLAlchemyError as error:
        logger.error("drift_policy_read_failed policy_id=%s", policy_id)
        raise _persistence_error() from error
    if result is None:
        raise _error(404, "DRIFT_POLICY_NOT_FOUND", "drift policy was not found")
    return result


@comparison_router.post("", response_model=DriftComparison, status_code=201)
async def create_comparison(
    payload: DriftComparisonCreate, session: DatabaseSession
) -> DriftComparison:
    try:
        return await create_drift_comparison(session, payload)
    except DriftPolicyNotFound as error:
        raise _error(404, "DRIFT_POLICY_NOT_FOUND", "drift policy was not found") from error
    except MonitoringSnapshotNotFound as error:
        raise _error(
            404, "MONITORING_SNAPSHOT_NOT_FOUND", "monitoring snapshot was not found"
        ) from error
    except DriftSnapshotsMustDiffer as error:
        raise _error(
            422, "DRIFT_SNAPSHOTS_MUST_DIFFER", "baseline and current snapshots must differ"
        ) from error
    except DriftSnapshotNotCompleted as error:
        raise _error(
            409,
            "DRIFT_SNAPSHOT_NOT_COMPLETED",
            "baseline and current snapshots must be completed",
        ) from error
    except DriftMonitorMismatch as error:
        raise _error(
            422,
            "DRIFT_MONITOR_MISMATCH",
            "baseline and current snapshots must belong to the same monitoring definition",
        ) from error
    except DriftWindowDurationMismatch as error:
        raise _error(
            422,
            "DRIFT_WINDOW_DURATION_MISMATCH",
            "baseline and current snapshots must use the same window duration",
        ) from error
    except DriftBaselineNotBeforeCurrent as error:
        raise _error(
            422,
            "DRIFT_BASELINE_NOT_BEFORE_CURRENT",
            "baseline snapshot must precede current snapshot",
        ) from error
    except DriftWindowsOverlap as error:
        raise _error(
            422, "DRIFT_WINDOWS_OVERLAP", "baseline and current windows must not overlap"
        ) from error
    except DriftPolicyEmpty as error:
        raise _error(409, "DRIFT_POLICY_EMPTY", "drift policy has no rules") from error
    except SQLAlchemyError as error:
        logger.error("drift_comparison_create_failed")
        raise _persistence_error() from error


@comparison_router.get("", response_model=DriftComparisonList)
async def read_comparisons(
    session: DatabaseSession,
    params: Annotated[DriftComparisonListParams, Query()],
) -> DriftComparisonList:
    try:
        return await list_drift_comparisons(session, params)
    except SQLAlchemyError as error:
        logger.error("drift_comparison_list_failed")
        raise _persistence_error() from error


@comparison_router.get("/{comparison_id}", response_model=DriftComparison)
async def read_comparison(comparison_id: UUID, session: DatabaseSession) -> DriftComparison:
    try:
        result = await get_drift_comparison(session, comparison_id)
    except SQLAlchemyError as error:
        logger.error("drift_comparison_read_failed comparison_id=%s", comparison_id)
        raise _persistence_error() from error
    if result is None:
        raise _error(404, "DRIFT_COMPARISON_NOT_FOUND", "drift comparison was not found")
    return result
