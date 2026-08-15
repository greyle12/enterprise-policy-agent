from __future__ import annotations

from pydantic import BaseModel, Field

from app.cache import CacheProviderName, CacheStateName


class LLMCacheMetricsResponse(BaseModel):
    """Process-local LLM cache counters since application startup."""

    hits: int = Field(ge=0)
    misses: int = Field(ge=0)
    writes: int = Field(ge=0)
    bypasses: int = Field(ge=0)
    errors: int = Field(ge=0)
    coalesced: int = Field(ge=0)
    singleflight_overflows: int = Field(ge=0)


class LLMCacheStatusResponse(BaseModel):
    """Operator-safe status with no Redis URL, prompt, or cache key."""

    provider: CacheProviderName
    state: CacheStateName
    available: bool
    ttl_seconds: int = Field(ge=1)
    singleflight_enabled: bool
    singleflight_max_keys: int = Field(ge=1)
    singleflight_in_flight: int = Field(ge=0)
    metrics: LLMCacheMetricsResponse
