from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from agentscope_api.core.config import get_settings


class Base(DeclarativeBase):
    pass


engine: AsyncEngine = create_async_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    # NullPool opens every test connection from scratch; allow brief local runtime stalls.
    connect_args={"timeout": 5 if get_settings().app_env == "test" else 2},
    poolclass=NullPool if get_settings().app_env == "test" else AsyncAdaptedQueuePool,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


DatabaseSession = Annotated[AsyncSession, Depends(get_session)]
