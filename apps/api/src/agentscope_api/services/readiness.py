import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text

from agentscope_api.core.config import get_settings
from agentscope_api.database import engine

DependencyState = Literal["ok", "unavailable"]


class DependencyStatus(BaseModel):
    postgres: DependencyState
    redis: DependencyState


async def _postgres_available() -> bool:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return True


async def _redis_available() -> bool:
    timeout = get_settings().dependency_probe_timeout_seconds
    client = Redis.from_url(
        get_settings().redis_url,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )
    try:
        await client.ping()
        return True
    finally:
        await client.aclose()


async def _bounded(probe: Callable[[], Awaitable[bool]], timeout_seconds: float) -> bool:
    try:
        async with asyncio.timeout(timeout_seconds):
            return await probe()
    except Exception:
        return False


async def check_readiness(timeout_seconds: float) -> DependencyStatus:
    postgres_ok, redis_ok = await asyncio.gather(
        _bounded(_postgres_available, timeout_seconds),
        _bounded(_redis_available, timeout_seconds),
    )
    return DependencyStatus(
        postgres="ok" if postgres_ok else "unavailable",
        redis="ok" if redis_ok else "unavailable",
    )


async def get_readiness() -> DependencyStatus:
    return await check_readiness(get_settings().dependency_probe_timeout_seconds)
