from functools import lru_cache
from pathlib import Path
from tempfile import gettempdir
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "AgentScope API"
    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = (
        "postgresql+asyncpg://agentscope:local-development-only@localhost:5432/agentscope"
    )
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:3000"
    dependency_probe_timeout_seconds: float = Field(default=1.0, ge=0.1, le=5.0)
    evaluation_recovery_interval_seconds: int = Field(default=60, ge=10, le=3_600)
    monitoring_max_catchup_windows: int = Field(default=24, ge=1, le=168)
    monitoring_max_drift_checks_per_scan: int = Field(default=100, ge=1, le=500)
    bisection_max_commit_range: int = Field(default=500, ge=1, le=5_000)
    bisection_work_root: str = Field(
        default_factory=lambda: str(Path(gettempdir()) / "agentscope-bisection-worktrees"),
        min_length=1,
        max_length=2_048,
    )
    bisection_execution_max_attempts: int = Field(default=3, ge=1, le=3)
    openai_api_key: SecretStr | None = None

    @field_validator("database_url")
    @classmethod
    def require_async_postgres(cls, value: str) -> str:
        try:
            parsed = make_url(value.strip())
        except Exception as error:
            raise ValueError("DATABASE_URL must be a valid SQLAlchemy URL") from error
        if parsed.drivername != "postgresql+asyncpg" or not parsed.database:
            raise ValueError("DATABASE_URL must use postgresql+asyncpg")
        return value.strip()

    @field_validator("redis_url")
    @classmethod
    def require_redis(cls, value: str) -> str:
        try:
            parsed = urlsplit(value.strip())
            _ = parsed.port
        except ValueError as error:
            raise ValueError("REDIS_URL must be a valid Redis URL") from error
        if parsed.scheme not in {"redis", "rediss"} or parsed.hostname is None:
            raise ValueError("REDIS_URL must use redis or rediss")
        return value.strip()

    @field_validator("cors_origins")
    @classmethod
    def require_explicit_origins(cls, value: str) -> str:
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        if not origins:
            raise ValueError("CORS_ORIGINS must contain at least one explicit origin")
        normalized: list[str] = []
        for origin in origins:
            if origin == "*":
                raise ValueError("wildcard CORS origins are not allowed")
            parsed = urlsplit(origin)
            try:
                _ = parsed.port
            except ValueError as error:
                raise ValueError("CORS_ORIGINS contains an invalid origin") from error
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS_ORIGINS must contain HTTP(S) origins without paths")
            normalized.append(f"{parsed.scheme}://{parsed.netloc}")
        return ",".join(normalized)

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def blank_openai_key_is_unconfigured(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def reject_default_password_in_production(self) -> Self:
        if self.app_env == "production" and "local-development-only" in self.database_url:
            raise ValueError("Production must not use the development database password")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
