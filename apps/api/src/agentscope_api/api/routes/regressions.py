from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agentscope_api.database import DatabaseSession
from agentscope_api.schemas.regressions import (
    RegressionCheck,
    RegressionCheckCreate,
    RegressionCheckList,
    RegressionCheckListParams,
    RegressionPolicy,
    RegressionPolicyCreate,
    RegressionPolicyList,
    RegressionPolicyListParams,
)
from agentscope_api.services.regressions import (
    DuplicateRegressionCheck,
    IncompleteRegressionEvidence,
    RegressionAnalysisRequired,
    RegressionPolicyNotFound,
    RegressionSourceRunNotCompleted,
    RegressionSourceRunNotFound,
    create_regression_check,
    create_regression_policy,
    get_regression_check,
    get_regression_policy,
    list_regression_checks,
    list_regression_policies,
)

router = APIRouter(prefix="/api/v1/regression-policies", tags=["regression-policies"])
checks_router = APIRouter(prefix="/api/v1/regression-checks", tags=["regression-checks"])
logger = logging.getLogger(__name__)


def _persistence_error() -> HTTPException:
    return HTTPException(
        status_code=500,
        detail={"code": "PERSISTENCE_ERROR", "message": "regression persistence failed"},
    )


@router.post("", response_model=RegressionPolicy, status_code=201)
async def create(payload: RegressionPolicyCreate, session: DatabaseSession) -> RegressionPolicy:
    try:
        return await create_regression_policy(session, payload)
    except SQLAlchemyError as error:
        logger.error("regression_policy_create_failed")
        raise _persistence_error() from error


@router.get("", response_model=RegressionPolicyList)
async def read_many(
    session: DatabaseSession,
    params: Annotated[RegressionPolicyListParams, Query()],
) -> RegressionPolicyList:
    try:
        return await list_regression_policies(session, params)
    except SQLAlchemyError as error:
        logger.error("regression_policy_list_failed")
        raise _persistence_error() from error


@router.get("/{policy_id}", response_model=RegressionPolicy)
async def read_one(policy_id: UUID, session: DatabaseSession) -> RegressionPolicy:
    try:
        policy = await get_regression_policy(session, policy_id)
    except SQLAlchemyError as error:
        logger.error("regression_policy_read_failed policy_id=%s", policy_id)
        raise _persistence_error() from error
    if policy is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "REGRESSION_POLICY_NOT_FOUND", "message": "policy was not found"},
        )
    return policy


def _check_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


@checks_router.post("", response_model=RegressionCheck, status_code=201)
async def create_check(payload: RegressionCheckCreate, session: DatabaseSession) -> RegressionCheck:
    try:
        return await create_regression_check(session, payload)
    except RegressionSourceRunNotFound as error:
        raise _check_error(
            404, "EXPERIMENT_RUN_NOT_FOUND", "experiment run was not found"
        ) from error
    except RegressionPolicyNotFound as error:
        raise _check_error(404, "REGRESSION_POLICY_NOT_FOUND", "policy was not found") from error
    except RegressionSourceRunNotCompleted as error:
        raise _check_error(
            409, "EXPERIMENT_RUN_NOT_COMPLETED", "experiment run must be completed"
        ) from error
    except RegressionAnalysisRequired as error:
        raise _check_error(
            409,
            "EXPERIMENT_ANALYSIS_REQUIRED",
            "the completed run must have a persisted analysis",
        ) from error
    except IncompleteRegressionEvidence as error:
        raise _check_error(
            409,
            "INCOMPLETE_REGRESSION_EVIDENCE",
            "persisted run analysis is incomplete",
        ) from error
    except DuplicateRegressionCheck as error:
        raise _check_error(
            409, "REGRESSION_CHECK_EXISTS", "this regression check already exists"
        ) from error
    except IntegrityError as error:
        logger.info("duplicate_regression_check run_id=%s", payload.experiment_run_id)
        raise _check_error(
            409, "REGRESSION_CHECK_EXISTS", "this regression check already exists"
        ) from error
    except SQLAlchemyError as error:
        logger.error("regression_check_create_failed run_id=%s", payload.experiment_run_id)
        raise _persistence_error() from error


@checks_router.get("", response_model=RegressionCheckList)
async def read_checks(
    session: DatabaseSession,
    params: Annotated[RegressionCheckListParams, Query()],
) -> RegressionCheckList:
    try:
        return await list_regression_checks(session, params)
    except SQLAlchemyError as error:
        logger.error("regression_check_list_failed")
        raise _persistence_error() from error


@checks_router.get("/{check_id}", response_model=RegressionCheck)
async def read_check(check_id: UUID, session: DatabaseSession) -> RegressionCheck:
    try:
        check = await get_regression_check(session, check_id)
    except SQLAlchemyError as error:
        logger.error("regression_check_read_failed check_id=%s", check_id)
        raise _persistence_error() from error
    if check is None:
        raise _check_error(404, "REGRESSION_CHECK_NOT_FOUND", "regression check was not found")
    return check
