from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CacheProviderName(StrEnum):
    """Supported LLM response-cache providers."""

    DISABLED = "disabled"
    REDIS = "redis"


class CacheStateName(StrEnum):
    """Operator-facing state of the optional LLM cache."""

    DISABLED = "disabled"
    AVAILABLE = "available"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class LLMCacheMetricsSnapshot:
    """Process-local counters for cache decisions and outcomes."""

    hits: int
    misses: int
    writes: int
    bypasses: int
    errors: int
    coalesced: int
    singleflight_overflows: int


@dataclass(frozen=True, slots=True)
class LLMCacheStatus:
    """Safe cache status that contains no Redis URL, keys, or prompt text."""

    provider: CacheProviderName
    state: CacheStateName
    available: bool
    ttl_seconds: int
    singleflight_enabled: bool
    singleflight_max_keys: int
    singleflight_in_flight: int
    metrics: LLMCacheMetricsSnapshot
