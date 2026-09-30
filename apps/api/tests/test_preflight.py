from pathlib import Path

from agentscope_api.core.config import Settings
from agentscope_api.preflight import MigrationState, _alembic_config, run_preflight
from agentscope_api.services.readiness import DependencyStatus


def production_settings() -> Settings:
    return Settings(
        app_env="production",
        database_url="postgresql+asyncpg://agentscope:strong-password@postgres/agentscope",
        redis_url="rediss://redis.example.test:6380/0",
        cors_origins="https://agentscope.example.test",
    )


def test_alembic_config_resolves_scripts_outside_api_working_directory(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    config = _alembic_config()

    assert Path(config.get_main_option("script_location")).is_dir()


async def test_preflight_passes_for_ready_current_installation() -> None:
    async def readiness() -> DependencyStatus:
        return DependencyStatus(postgres="ok", redis="ok")

    async def migrations() -> MigrationState:
        return MigrationState(expected=("20260925_0018",), current=("20260925_0018",))

    checks = await run_preflight(
        settings=production_settings(),
        readiness_check=readiness,
        migration_check=migrations,
    )

    assert [check.status for check in checks] == ["PASS", "PASS", "PASS", "PASS", "WARNING"]
    assert all(check.status != "FAIL" for check in checks)


async def test_preflight_fails_for_migration_mismatch() -> None:
    async def readiness() -> DependencyStatus:
        return DependencyStatus(postgres="ok", redis="ok")

    async def migrations() -> MigrationState:
        return MigrationState(expected=("20260925_0018",), current=("20260924_0017",))

    checks = await run_preflight(
        settings=production_settings(),
        readiness_check=readiness,
        migration_check=migrations,
    )

    migration = next(check for check in checks if check.name == "migrations")
    assert migration.status == "FAIL"
    assert migration.detail == "current=20260924_0017 expected=20260925_0018"


async def test_preflight_reports_dependency_failures_without_urls() -> None:
    secret = "do-not-print-this-password"
    settings = Settings(
        database_url=f"postgresql+asyncpg://agentscope:{secret}@postgres/agentscope",
        redis_url=f"redis://:{secret}@redis:6379/0",
    )

    async def readiness() -> DependencyStatus:
        return DependencyStatus(postgres="unavailable", redis="unavailable")

    async def migrations() -> MigrationState:
        raise RuntimeError(f"postgresql+asyncpg://agentscope:{secret}@postgres/agentscope")

    checks = await run_preflight(
        settings=settings,
        readiness_check=readiness,
        migration_check=migrations,
    )
    output = "\n".join(check.render() for check in checks)

    assert "[FAIL] postgres" in output
    assert "[FAIL] redis" in output
    assert "[FAIL] migrations" in output
    assert secret not in output
