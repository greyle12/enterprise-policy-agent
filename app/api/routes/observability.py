from __future__ import annotations

import logging
from typing import Protocol, cast

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.schemas.observability import (
    HttpDurationBucketResponse,
    HttpRouteMetricsResponse,
    HttpStatusCountResponse,
    RuntimeObservabilityStatusResponse,
)
from app.llm import ProviderLimiterStatus
from app.observability import HttpMetricsRegistry, render_prometheus_metrics
from app.security import PromptInjectionGuard

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/observability",
    tags=["observability"],
)
metrics_router = APIRouter()


class _ProviderLimiterStatusProvider(Protocol):
    async def status(self) -> ProviderLimiterStatus: ...


def _registry(request: Request) -> HttpMetricsRegistry:
    registry = getattr(request.app.state, "http_metrics", None)
    if not isinstance(registry, HttpMetricsRegistry):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HTTP observability is not initialized",
        )
    return registry


@router.get(
    "/status",
    response_model=RuntimeObservabilityStatusResponse,
    summary="Inspect safe process-local HTTP telemetry",
)
async def observability_status(request: Request) -> RuntimeObservabilityStatusResponse:
    snapshot = _registry(request).snapshot()
    return RuntimeObservabilityStatusResponse(
        requests_total=snapshot.requests_total,
        in_flight=snapshot.in_flight,
        peak_in_flight=snapshot.peak_in_flight,
        max_route_keys=snapshot.max_route_keys,
        tracked_route_keys=snapshot.tracked_route_keys,
        route_overflow_requests=snapshot.route_overflow_requests,
        recording_errors=snapshot.recording_errors,
        duration_buckets_seconds=list(snapshot.duration_buckets_seconds),
        routes=[
            HttpRouteMetricsResponse(
                method=route.method,
                route=route.route,
                requests=route.requests,
                status_counts=[
                    HttpStatusCountResponse(
                        status_class=item.status_class,
                        count=item.count,
                    )
                    for item in route.status_counts
                ],
                duration_buckets=[
                    HttpDurationBucketResponse(
                        upper_bound_seconds=item.upper_bound_seconds,
                        count=item.count,
                    )
                    for item in route.duration_buckets
                ],
                duration_sum_ms=round(route.duration_sum_seconds * 1000, 3),
                average_duration_ms=round(route.average_duration_seconds * 1000, 3),
                max_duration_ms=round(route.max_duration_seconds * 1000, 3),
            )
            for route in snapshot.routes
        ],
    )


@metrics_router.get(
    "/metrics",
    include_in_schema=False,
)
async def prometheus_metrics(request: Request) -> Response:
    """Export safe bounded metrics in Prometheus text format 0.0.4."""

    http_snapshot = _registry(request).snapshot()
    provider_snapshot: ProviderLimiterStatus | None = None
    provider = getattr(request.app.state, "llm_provider_limiter", None)
    if provider is not None and hasattr(provider, "status"):
        try:
            provider_snapshot = await cast(
                _ProviderLimiterStatusProvider,
                provider,
            ).status()
        except Exception as error:
            logger.warning(
                "provider_metrics_snapshot_failed",
                extra={"error_type": type(error).__name__},
            )

    guard = getattr(request.app.state, "prompt_security_guard", None)
    prompt_security_snapshot = guard.snapshot() if isinstance(guard, PromptInjectionGuard) else None

    content = render_prometheus_metrics(
        http_snapshot,
        provider=provider_snapshot,
        prompt_security=prompt_security_snapshot,
    )
    return Response(
        content=content,
        headers={
            "Cache-Control": "no-store",
            "Content-Type": "text/plain; version=0.0.4; charset=utf-8",
        },
    )
