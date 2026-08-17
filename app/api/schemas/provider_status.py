from __future__ import annotations

from pydantic import BaseModel, Field

from app.llm import ProviderLimiterStateName


class ProviderLimiterMetricsResponse(BaseModel):
    """Process-local provider-capacity counters since application startup."""

    requests: int = Field(ge=0)
    bypassed: int = Field(ge=0)
    accepted: int = Field(ge=0)
    started: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    rejected: int = Field(ge=0)
    timed_out: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    peak_in_flight: int = Field(ge=0)
    peak_queued: int = Field(ge=0)
    average_wait_ms: float = Field(ge=0)


class ProviderLimiterStatusResponse(BaseModel):
    """Operator-safe provider limiter state with no prompt or credential data."""

    enabled: bool
    state: ProviderLimiterStateName
    max_concurrency: int = Field(ge=1)
    max_queue: int = Field(ge=0)
    queue_timeout_seconds: float = Field(gt=0)
    in_flight: int = Field(ge=0)
    queued: int = Field(ge=0)
    metrics: ProviderLimiterMetricsResponse
