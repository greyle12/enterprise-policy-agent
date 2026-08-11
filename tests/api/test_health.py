from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


class HealthyDatabaseProbe:
    async def ping(self) -> None:
        return None


class FailingDatabaseProbe:
    async def ping(self) -> None:
        raise RuntimeError("sensitive database path must not reach clients")


def _configure_components(application, database_probe) -> None:
    application.state.policy_answer_service = object()
    application.state.agent_router = object()
    application.state.agent_state_store = database_probe


def test_liveness_does_not_require_initialized_dependencies() -> None:
    application = create_app(enable_lifespan=False)

    with TestClient(application) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "Enterprise Policy Agent",
        "version": "0.1.0",
    }


def test_readiness_returns_503_before_lifespan_initialization() -> None:
    application = create_app(enable_lifespan=False)

    with TestClient(application) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "application": "unavailable",
            "database": "not_checked",
        },
    }


def test_readiness_returns_200_when_components_and_database_are_ready() -> None:
    application = create_app(enable_lifespan=False)
    _configure_components(application, HealthyDatabaseProbe())

    with TestClient(application) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "application": "ok",
            "database": "ok",
        },
    }


def test_readiness_hides_database_failure_details() -> None:
    application = create_app(enable_lifespan=False)
    _configure_components(application, FailingDatabaseProbe())

    with TestClient(application) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "application": "ok",
            "database": "unavailable",
        },
    }
    assert "sensitive database path" not in response.text


def test_openapi_exposes_both_health_endpoints() -> None:
    application = create_app(enable_lifespan=False)

    schema = application.openapi()

    assert "/health/live" in schema["paths"]
    assert "/health/ready" in schema["paths"]
