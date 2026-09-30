import pytest
from pydantic import ValidationError

from agentscope_api.core.config import Settings


def test_settings_accept_async_postgres_and_redis_urls() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:password@localhost/agentscope",
        redis_url="redis://localhost:6379/0",
    )

    assert settings.cors_origin_list == ["http://localhost:3000"]
    assert settings.evaluation_recovery_interval_seconds == 60
    assert settings.monitoring_max_catchup_windows == 24
    assert settings.monitoring_max_drift_checks_per_scan == 100


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_url", "postgresql://localhost/agentscope"),
        ("redis_url", "https://localhost:6379/0"),
    ],
)
def test_settings_reject_incompatible_dependency_urls(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})  # type: ignore[arg-type]


def test_settings_reject_development_password_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production")


@pytest.mark.parametrize("interval", [9, 3_601])
def test_settings_bound_periodic_recovery_interval(interval: int) -> None:
    with pytest.raises(ValidationError):
        Settings(evaluation_recovery_interval_seconds=interval)


def test_blank_openai_key_is_unconfigured() -> None:
    assert Settings(openai_api_key="").openai_api_key is None
    assert Settings(openai_api_key="   ").openai_api_key is None


def test_production_does_not_require_openai_credentials() -> None:
    settings = Settings(
        app_env="production",
        database_url="postgresql+asyncpg://agentscope:strong-password@postgres/agentscope",
        redis_url="rediss://redis.example.test:6380/0",
        cors_origins="https://agentscope.example.test",
    )

    assert settings.openai_api_key is None


@pytest.mark.parametrize(
    "origins",
    [
        "*",
        "https://user:password@agentscope.example.test",
        "https://agentscope.example.test/path",
        "https://agentscope.example.test?debug=true",
        "not-an-origin",
        "",
    ],
)
def test_settings_reject_unsafe_or_malformed_cors_origins(origins: str) -> None:
    with pytest.raises(ValidationError):
        Settings(cors_origins=origins)


def test_production_rejects_wildcard_cors() -> None:
    with pytest.raises(ValidationError, match="wildcard CORS"):
        Settings(
            app_env="production",
            database_url="postgresql+asyncpg://agentscope:strong-password@postgres/agentscope",
            cors_origins="*",
        )


def test_settings_normalize_cors_values_to_browser_origins() -> None:
    settings = Settings(
        cors_origins="https://agentscope.example.test/, http://localhost:3000/"
    )

    assert settings.cors_origin_list == [
        "https://agentscope.example.test",
        "http://localhost:3000",
    ]


def test_dependency_validation_does_not_echo_credentials() -> None:
    secret = "do-not-print-this-password"

    with pytest.raises(ValidationError) as caught:
        Settings(database_url=f"mysql://user:{secret}@database/agentscope")

    assert secret not in str(caught.value)


@pytest.mark.parametrize("maximum", [0, 169])
def test_settings_bound_monitoring_catch_up(maximum: int) -> None:
    with pytest.raises(ValidationError):
        Settings(monitoring_max_catchup_windows=maximum)


@pytest.mark.parametrize("maximum", [0, 501])
def test_settings_bound_automatic_drift_scan(maximum: int) -> None:
    with pytest.raises(ValidationError):
        Settings(monitoring_max_drift_checks_per_scan=maximum)
