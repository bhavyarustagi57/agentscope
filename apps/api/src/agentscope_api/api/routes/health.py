from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agentscope_api.services.readiness import DependencyStatus, get_readiness

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    dependencies: DependencyStatus


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
async def ready(
    dependencies: Annotated[DependencyStatus, Depends(get_readiness)],
) -> ReadinessResponse | JSONResponse:
    is_ready = dependencies.postgres == "ok" and dependencies.redis == "ok"
    response = ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        dependencies=dependencies,
    )
    if is_ready:
        return response
    return JSONResponse(status_code=503, content=response.model_dump())
