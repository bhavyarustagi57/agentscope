from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import text

from agentscope_api.core.config import Settings
from agentscope_api.services.readiness import DependencyStatus, check_readiness

CheckStatus = Literal["PASS", "WARNING", "FAIL"]
ReadinessCheck = Callable[[], Awaitable[DependencyStatus]]
MigrationCheck = Callable[[], Awaitable["MigrationState"]]


@dataclass(frozen=True)
class Check:
    status: CheckStatus
    name: str
    detail: str

    def render(self) -> str:
        return f"[{self.status}] {self.name}: {self.detail}"


@dataclass(frozen=True)
class MigrationState:
    expected: tuple[str, ...]
    current: tuple[str, ...]


def _alembic_config() -> Config:
    candidates = (Path.cwd() / "alembic.ini", Path(__file__).resolve().parents[2] / "alembic.ini")
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise RuntimeError("alembic.ini is unavailable")
    config = Config(str(path))
    script_location_value = config.get_main_option("script_location")
    if script_location_value is None:
        raise RuntimeError("Alembic script_location is unavailable")
    script_location = Path(script_location_value)
    if not script_location.is_absolute():
        config.set_main_option("script_location", str((path.parent / script_location).resolve()))
    return config


async def read_migration_state() -> MigrationState:
    from agentscope_api.database import engine

    expected = tuple(ScriptDirectory.from_config(_alembic_config()).get_heads())
    async with engine.connect() as connection:
        rows = await connection.execute(text("SELECT version_num FROM alembic_version"))
        current = tuple(sorted(str(row[0]) for row in rows))
    return MigrationState(expected=tuple(sorted(expected)), current=current)


async def run_preflight(
    *,
    settings: Settings,
    readiness_check: ReadinessCheck | None = None,
    migration_check: MigrationCheck | None = None,
) -> list[Check]:
    if readiness_check is None:
        async def configured_readiness() -> DependencyStatus:
            return await check_readiness(settings.dependency_probe_timeout_seconds)

        readiness_check = configured_readiness
    if migration_check is None:
        migration_check = read_migration_state

    checks = [Check("PASS", "configuration", f"valid for {settings.app_env}")]
    dependencies = await readiness_check()
    checks.extend(
        Check("PASS" if state == "ok" else "FAIL", name, state)
        for name, state in (
            ("postgres", dependencies.postgres),
            ("redis", dependencies.redis),
        )
    )

    if dependencies.postgres == "ok":
        try:
            migrations = await migration_check()
        except Exception:
            checks.append(Check("FAIL", "migrations", "unable to inspect database revision"))
        else:
            current = ",".join(migrations.current) or "none"
            expected = ",".join(migrations.expected) or "none"
            checks.append(
                Check(
                    "PASS" if migrations.current == migrations.expected else "FAIL",
                    "migrations",
                    f"current={current} expected={expected}",
                )
            )
    else:
        checks.append(Check("FAIL", "migrations", "not checked because PostgreSQL is unavailable"))

    checks.append(
        Check(
            "PASS" if settings.openai_api_key is not None else "WARNING",
            "openai",
            "configured" if settings.openai_api_key is not None else "judge execution is disabled",
        )
    )
    return checks


def _validation_detail(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
        for issue in error.errors(include_input=False, include_url=False)
    )


def main() -> int:
    try:
        settings = Settings()
    except ValidationError as error:
        print(Check("FAIL", "configuration", _validation_detail(error)).render())
        return 1

    checks = asyncio.run(run_preflight(settings=settings))
    for check in checks:
        print(check.render())
    return int(any(check.status == "FAIL" for check in checks))


if __name__ == "__main__":
    raise SystemExit(main())
