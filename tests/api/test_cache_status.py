from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.schemas.cache_status import LLMCacheStatusResponse
from app.cache import (
    CacheProviderName,
    CacheStateName,
    LLMCacheMetricsSnapshot,
    LLMCacheStatus,
)
from app.main import create_app


class FakeCacheStatusProvider:
    async def cache_status(self) -> LLMCacheStatus:
        return LLMCacheStatus(
            provider=CacheProviderName.REDIS,
            state=CacheStateName.AVAILABLE,
            available=True,
            ttl_seconds=600,
            metrics=LLMCacheMetricsSnapshot(
                hits=3,
                misses=2,
                writes=2,
                bypasses=1,
                errors=0,
                coalesced=4,
                singleflight_overflows=0,
            ),
            singleflight_enabled=True,
            singleflight_max_keys=128,
            singleflight_in_flight=0,
        )


def test_cache_status_exposes_safe_operational_fields() -> None:
    application = create_app(enable_lifespan=False)
    application.state.llm_cache = FakeCacheStatusProvider()

    with TestClient(application) as client:
        response = client.get("/api/v1/cache/status")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "redis",
        "state": "available",
        "available": True,
        "ttl_seconds": 600,
        "singleflight_enabled": True,
        "singleflight_max_keys": 128,
        "singleflight_in_flight": 0,
        "metrics": {
            "hits": 3,
            "misses": 2,
            "writes": 2,
            "bypasses": 1,
            "errors": 0,
            "coalesced": 4,
            "singleflight_overflows": 0,
        },
    }
    serialized = response.text.lower()
    assert "redis_url" not in serialized
    assert "prompt" not in serialized


def test_cache_status_requires_initialized_lifespan() -> None:
    application = create_app(enable_lifespan=False)

    with TestClient(application) as client:
        response = client.get("/api/v1/cache/status")

    assert response.status_code == 503
    assert response.json() == {"detail": "LLM cache is not initialized"}


def test_openapi_exposes_cache_status_contract() -> None:
    application = create_app(enable_lifespan=False)
    schema = application.openapi()

    assert "/api/v1/cache/status" in schema["paths"]
    assert "LLMCacheStatusResponse" in schema["components"]["schemas"]
    LLMCacheStatusResponse.model_validate(
        {
            "provider": "disabled",
            "state": "disabled",
            "available": False,
            "ttl_seconds": 600,
            "singleflight_enabled": False,
            "singleflight_max_keys": 128,
            "singleflight_in_flight": 0,
            "metrics": {
                "hits": 0,
                "misses": 0,
                "writes": 0,
                "bypasses": 0,
                "errors": 0,
                "coalesced": 0,
                "singleflight_overflows": 0,
            },
        }
    )
