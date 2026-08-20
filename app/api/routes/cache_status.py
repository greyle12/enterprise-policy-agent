from __future__ import annotations

from typing import Protocol, cast

from fastapi import APIRouter, HTTPException, Request, status

from app.api.schemas.cache_status import (
    LLMCacheMetricsResponse,
    LLMCacheStatusResponse,
)
from app.cache import LLMCacheStatus

router = APIRouter(
    prefix="/cache",
    tags=["cache"],
)


class _CacheStatusProvider(Protocol):
    async def cache_status(self) -> LLMCacheStatus: ...


@router.get(
    "/status",
    response_model=LLMCacheStatusResponse,
    summary="Inspect the optional LLM response cache",
)
async def cache_status(request: Request) -> LLMCacheStatusResponse:
    """Return safe Redis availability and process-local cache counters."""

    provider = getattr(request.app.state, "llm_cache", None)
    if provider is None or not hasattr(provider, "cache_status"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM cache is not initialized",
        )
    snapshot = await cast(_CacheStatusProvider, provider).cache_status()
    return LLMCacheStatusResponse(
        provider=snapshot.provider,
        state=snapshot.state,
        available=snapshot.available,
        ttl_seconds=snapshot.ttl_seconds,
        singleflight_enabled=snapshot.singleflight_enabled,
        singleflight_max_keys=snapshot.singleflight_max_keys,
        singleflight_in_flight=snapshot.singleflight_in_flight,
        metrics=LLMCacheMetricsResponse(
            hits=snapshot.metrics.hits,
            misses=snapshot.metrics.misses,
            writes=snapshot.metrics.writes,
            bypasses=snapshot.metrics.bypasses,
            errors=snapshot.metrics.errors,
            coalesced=snapshot.metrics.coalesced,
            singleflight_overflows=snapshot.metrics.singleflight_overflows,
        ),
    )
