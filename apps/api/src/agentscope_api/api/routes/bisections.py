from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agentscope_api.database import DatabaseSession
from agentscope_api.schemas.bisections import (
    BisectionSession,
    BisectionSessionCreate,
    BisectionSessionList,
    BisectionSessionListParams,
)
from agentscope_api.services.bisections import (
    BaselineGitRevisionRequired,
    CandidateGitRevisionRequired,
    DuplicateBisectionSession,
    RegressionCheckNotFound,
    RegressionEvidenceChanged,
    RegressionNotDetected,
    create_bisection_session,
    get_bisection_session,
    list_bisection_sessions,
)
from agentscope_api.services.git_inspection import (
    BaselineNotAncestor,
    BisectionEndpointsIdentical,
    CommitRangeTooLarge,
    GitCommandFailed,
    GitCommandTimeout,
    GitInspectionError,
    GitRepositoryNotFound,
    MalformedGitOutput,
    NotGitRepository,
    RevisionNotFound,
    UnsafeRepositoryState,
)

router = APIRouter(prefix="/api/v1/bisection-sessions", tags=["bisection-sessions"])
logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


@router.post("", response_model=BisectionSession, status_code=201)
async def create(payload: BisectionSessionCreate, session: DatabaseSession) -> BisectionSession:
    try:
        return await create_bisection_session(session, payload)
    except RegressionCheckNotFound as error:
        raise _error(404, "REGRESSION_CHECK_NOT_FOUND", "regression check was not found") from error
    except RegressionNotDetected as error:
        raise _error(
            409, "REGRESSION_NOT_DETECTED", "regression check is not regression detected"
        ) from error
    except BaselineGitRevisionRequired as error:
        raise _error(
            409,
            "BASELINE_GIT_REVISION_REQUIRED",
            "frozen baseline provenance has no usable Git revision",
        ) from error
    except CandidateGitRevisionRequired as error:
        raise _error(
            409,
            "CANDIDATE_GIT_REVISION_REQUIRED",
            "frozen candidate provenance has no usable Git revision",
        ) from error
    except GitRepositoryNotFound as error:
        raise _error(422, "GIT_REPOSITORY_NOT_FOUND", "repository path was not found") from error
    except NotGitRepository as error:
        raise _error(422, "NOT_A_GIT_REPOSITORY", "path is not a Git repository") from error
    except RevisionNotFound as error:
        code = (
            "BASELINE_REVISION_NOT_FOUND"
            if error.endpoint == "baseline"
            else "CANDIDATE_REVISION_NOT_FOUND"
        )
        raise _error(422, code, f"{error.endpoint} revision was not found") from error
    except BisectionEndpointsIdentical as error:
        raise _error(
            422, "BISECTION_ENDPOINTS_IDENTICAL", "baseline and candidate resolve identically"
        ) from error
    except BaselineNotAncestor as error:
        raise _error(
            422,
            "BASELINE_NOT_ANCESTOR",
            "baseline commit is not an ancestor of candidate commit",
        ) from error
    except CommitRangeTooLarge as error:
        raise _error(
            422, "COMMIT_RANGE_TOO_LARGE", "commit range exceeds the configured maximum"
        ) from error
    except GitCommandTimeout as error:
        raise _error(504, "GIT_COMMAND_TIMEOUT", "Git inspection timed out") from error
    except MalformedGitOutput as error:
        raise _error(422, "MALFORMED_GIT_OUTPUT", "Git returned malformed metadata") from error
    except UnsafeRepositoryState as error:
        raise _error(
            422,
            "UNSAFE_GIT_REPOSITORY",
            "repository state cannot support reliable bisection planning",
        ) from error
    except (RegressionEvidenceChanged, DuplicateBisectionSession) as error:
        code = (
            "REGRESSION_EVIDENCE_CHANGED"
            if isinstance(error, RegressionEvidenceChanged)
            else "BISECTION_SESSION_EXISTS"
        )
        raise _error(409, code, "bisection session could not be created") from error
    except GitCommandFailed as error:
        raise _error(422, "GIT_INSPECTION_FAILED", "Git inspection failed") from error
    except GitInspectionError as error:
        raise _error(422, "GIT_INSPECTION_FAILED", "Git inspection failed") from error
    except IntegrityError as error:
        raise _error(409, "BISECTION_SESSION_EXISTS", "bisection session already exists") from error
    except SQLAlchemyError as error:
        logger.error("bisection_session_create_failed check_id=%s", payload.regression_check_id)
        raise _error(500, "PERSISTENCE_ERROR", "bisection persistence failed") from error


@router.get("", response_model=BisectionSessionList)
async def read_many(
    session: DatabaseSession,
    params: Annotated[BisectionSessionListParams, Query()],
) -> BisectionSessionList:
    try:
        return await list_bisection_sessions(session, params)
    except SQLAlchemyError as error:
        logger.error("bisection_session_list_failed")
        raise _error(500, "PERSISTENCE_ERROR", "bisection persistence failed") from error


@router.get("/{session_id}", response_model=BisectionSession)
async def read_one(session_id: UUID, session: DatabaseSession) -> BisectionSession:
    try:
        result = await get_bisection_session(session, session_id)
    except SQLAlchemyError as error:
        logger.error("bisection_session_read_failed session_id=%s", session_id)
        raise _error(500, "PERSISTENCE_ERROR", "bisection persistence failed") from error
    if result is None:
        raise _error(404, "BISECTION_SESSION_NOT_FOUND", "bisection session was not found")
    return result
