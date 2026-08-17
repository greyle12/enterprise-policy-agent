from __future__ import annotations

from typing import Protocol, cast

from fastapi import APIRouter, HTTPException, Request, status

from app.api.schemas.provider_status import (
    ProviderLimiterMetricsResponse,
    ProviderLimiterStatusResponse,
)
from app.llm import ProviderLimiterStatus

router = APIRouter(
    prefix="/provider",
    tags=["provider-capacity"],
)


class _ProviderLimiterStatusProvider(Protocol):
    async def status(self) -> ProviderLimiterStatus: ...


@router.get(
    "/status",
    response_model=ProviderLimiterStatusResponse,
    summary="Inspect process-local LLM provider capacity",
)
async def provider_status(request: Request) -> ProviderLimiterStatusResponse:
    """Return bounded-concurrency state and secret-free process counters."""

    provider = getattr(request.app.state, "llm_provider_limiter", None)
    if provider is None or not hasattr(provider, "status"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM provider capacity manager is not initialized",
        )
    snapshot = await cast(_ProviderLimiterStatusProvider, provider).status()
    return ProviderLimiterStatusResponse(
        enabled=snapshot.enabled,
        state=snapshot.state,
        max_concurrency=snapshot.max_concurrency,
        max_queue=snapshot.max_queue,
        queue_timeout_seconds=snapshot.queue_timeout_seconds,
        in_flight=snapshot.in_flight,
        queued=snapshot.queued,
        metrics=ProviderLimiterMetricsResponse(
            requests=snapshot.metrics.requests,
            bypassed=snapshot.metrics.bypassed,
            accepted=snapshot.metrics.accepted,
            started=snapshot.metrics.started,
            completed=snapshot.metrics.completed,
            failed=snapshot.metrics.failed,
            rejected=snapshot.metrics.rejected,
            timed_out=snapshot.metrics.timed_out,
            cancelled=snapshot.metrics.cancelled,
            peak_in_flight=snapshot.metrics.peak_in_flight,
            peak_queued=snapshot.metrics.peak_queued,
            average_wait_ms=snapshot.metrics.average_wait_ms,
        ),
    )
