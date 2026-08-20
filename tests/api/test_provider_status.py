from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.schemas.provider_status import ProviderLimiterStatusResponse
from app.llm import (
    ProviderLimiterMetricsSnapshot,
    ProviderLimiterStateName,
    ProviderLimiterStatus,
)
from app.main import create_app


class FakeProviderLimiter:
    async def status(self) -> ProviderLimiterStatus:
        return ProviderLimiterStatus(
            enabled=True,
            state=ProviderLimiterStateName.QUEUING,
            max_concurrency=4,
            max_queue=16,
            queue_timeout_seconds=2.0,
            in_flight=4,
            queued=3,
            metrics=ProviderLimiterMetricsSnapshot(
                requests=12,
                bypassed=0,
                accepted=11,
                started=8,
                completed=3,
                failed=1,
                rejected=1,
                timed_out=1,
                cancelled=0,
                peak_in_flight=4,
                peak_queued=5,
                average_wait_ms=12.345,
            ),
        )


def test_provider_status_exposes_only_safe_capacity_fields() -> None:
    application = create_app(enable_lifespan=False)
    application.state.llm_provider_limiter = FakeProviderLimiter()

    with TestClient(application) as client:
        response = client.get("/api/v1/provider/status")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "state": "queuing",
        "max_concurrency": 4,
        "max_queue": 16,
        "queue_timeout_seconds": 2.0,
        "in_flight": 4,
        "queued": 3,
        "metrics": {
            "requests": 12,
            "bypassed": 0,
            "accepted": 11,
            "started": 8,
            "completed": 3,
            "failed": 1,
            "rejected": 1,
            "timed_out": 1,
            "cancelled": 0,
            "peak_in_flight": 4,
            "peak_queued": 5,
            "average_wait_ms": 12.345,
        },
    }
    serialized = response.text.lower()
    assert "prompt" not in serialized
    assert "api_key" not in serialized
    assert "base_url" not in serialized


def test_provider_status_requires_initialized_lifespan() -> None:
    application = create_app(enable_lifespan=False)

    with TestClient(application) as client:
        response = client.get("/api/v1/provider/status")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "LLM provider capacity manager is not initialized"
    }


def test_openapi_exposes_provider_status_contract() -> None:
    application = create_app(enable_lifespan=False)
    schema = application.openapi()

    assert "/api/v1/provider/status" in schema["paths"]
    assert "ProviderLimiterStatusResponse" in schema["components"]["schemas"]
    ProviderLimiterStatusResponse.model_validate(
        {
            "enabled": False,
            "state": "disabled",
            "max_concurrency": 4,
            "max_queue": 16,
            "queue_timeout_seconds": 2.0,
            "in_flight": 0,
            "queued": 0,
            "metrics": {
                "requests": 0,
                "bypassed": 0,
                "accepted": 0,
                "started": 0,
                "completed": 0,
                "failed": 0,
                "rejected": 0,
                "timed_out": 0,
                "cancelled": 0,
                "peak_in_flight": 0,
                "peak_queued": 0,
                "average_wait_ms": 0,
            },
        }
    )
