import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agentscope_api.api.routes import router
from agentscope_api.core.config import get_settings
from agentscope_api.core.request_limits import RequestSizeLimitMiddleware
from agentscope_api.database import engine
from agentscope_api.schemas.traces import MAX_REQUEST_BYTES

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info("api_started app_env=%s", settings.app_env)
    try:
        yield
    finally:
        try:
            async with asyncio.timeout(5):
                await engine.dispose()
        except TimeoutError:
            logger.warning("api_shutdown_resource_cleanup_timed_out resource=database")
        logger.info("api_stopped app_env=%s", settings.app_env)


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["*"],
    )
    application.add_middleware(RequestSizeLimitMiddleware, max_bytes=MAX_REQUEST_BYTES)

    @application.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        details = [
            {
                "location": [str(item) for item in issue["loc"]],
                "message": issue["msg"],
                "type": issue["type"],
            }
            for issue in error.errors()
        ]
        unsupported = any("unsupported schema_version" in issue["message"] for issue in details)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "UNSUPPORTED_SCHEMA_VERSION" if unsupported else "VALIDATION_ERROR",
                    "message": "unsupported trace schema version"
                    if unsupported
                    else "request validation failed",
                    "details": details,
                }
            },
        )

    @application.exception_handler(HTTPException)
    async def http_error(_: Request, error: HTTPException) -> JSONResponse:
        detail = error.detail
        if isinstance(detail, dict) and "code" in detail:
            body = {"error": detail}
        else:
            body = {"error": {"code": "HTTP_ERROR", "message": str(detail)}}
        return JSONResponse(status_code=error.status_code, content=body, headers=error.headers)

    @application.exception_handler(Exception)
    async def internal_error(_: Request, error: Exception) -> JSONResponse:
        logger.error("request_failed category=%s", type(error).__name__)
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "INTERNAL_ERROR", "message": "internal server error"}
            },
        )

    application.include_router(router)
    return application


app = create_app()
