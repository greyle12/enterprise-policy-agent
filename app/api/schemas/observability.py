from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.observability import HttpStatusClassName


class HttpStatusCountResponse(BaseModel):
    status_class: HttpStatusClassName
    count: int = Field(ge=0)


class HttpDurationBucketResponse(BaseModel):
    upper_bound_seconds: float = Field(gt=0)
    count: int = Field(ge=0)


class HttpRouteMetricsResponse(BaseModel):
    method: str = Field(min_length=1, max_length=16)
    route: str = Field(min_length=1, max_length=200)
    requests: int = Field(ge=0)
    status_counts: list[HttpStatusCountResponse]
    duration_buckets: list[HttpDurationBucketResponse]
    duration_sum_ms: float = Field(ge=0)
    average_duration_ms: float = Field(ge=0)
    max_duration_ms: float = Field(ge=0)


class RuntimeObservabilityStatusResponse(BaseModel):
    """Safe process-local HTTP telemetry without request content or raw paths."""

    schema_version: Literal["1.0"] = "1.0"
    requests_total: int = Field(ge=0)
    in_flight: int = Field(ge=0)
    peak_in_flight: int = Field(ge=0)
    max_route_keys: int = Field(ge=1)
    tracked_route_keys: int = Field(ge=0)
    route_overflow_requests: int = Field(ge=0)
    recording_errors: int = Field(ge=0)
    duration_buckets_seconds: list[float]
    routes: list[HttpRouteMetricsResponse]
