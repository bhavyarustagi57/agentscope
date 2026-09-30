from fastapi.testclient import TestClient
from starlette.requests import Request

from agentscope_api.main import create_app
from agentscope_api.services.readiness import DependencyStatus, get_readiness


def test_health_reports_live_process() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_health_remains_live_when_readiness_dependency_is_unavailable() -> None:
    app = create_app()

    async def unavailable() -> DependencyStatus:
        raise RuntimeError("postgresql+asyncpg://user:secret@database/agentscope")

    app.dependency_overrides[get_readiness] = unavailable
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_available_dependencies() -> None:
    app = create_app()

    async def ready() -> DependencyStatus:
        return DependencyStatus(postgres="ok", redis="ok")

    app.dependency_overrides[get_readiness] = ready
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"postgres": "ok", "redis": "ok"},
    }


def test_ready_returns_503_when_a_dependency_is_unavailable() -> None:
    app = create_app()

    async def not_ready() -> DependencyStatus:
        return DependencyStatus(postgres="ok", redis="unavailable")

    app.dependency_overrides[get_readiness] = not_ready
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"postgres": "ok", "redis": "unavailable"},
    }


def test_ready_reports_recovery_after_dependency_outage() -> None:
    app = create_app()
    states = iter(
        [
            DependencyStatus(postgres="unavailable", redis="ok"),
            DependencyStatus(postgres="ok", redis="ok"),
        ]
    )

    async def changing_readiness() -> DependencyStatus:
        return next(states)

    app.dependency_overrides[get_readiness] = changing_readiness
    with TestClient(app) as client:
        unavailable = client.get("/ready")
        recovered = client.get("/ready")

    assert unavailable.status_code == 503
    assert recovered.status_code == 200


def test_unhandled_errors_are_sanitized() -> None:
    app = create_app()

    @app.get("/_test/failure")
    async def fail(_: Request) -> None:
        raise RuntimeError("postgresql+asyncpg://user:secret@database/agentscope")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/_test/failure")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "internal server error"}
    }
    assert "secret" not in response.text
