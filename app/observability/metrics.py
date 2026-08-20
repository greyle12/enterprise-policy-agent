from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from enum import StrEnum

DEFAULT_DURATION_BUCKETS_SECONDS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

_KNOWN_METHODS = frozenset(
    {
        "DELETE",
        "GET",
        "HEAD",
        "OPTIONS",
        "PATCH",
        "POST",
        "PUT",
    }
)
_UNMATCHED_ROUTE = "__unmatched__"
_OVERFLOW_ROUTE = "__overflow__"


class HttpStatusClassName(StrEnum):
    """Bounded status labels used by JSON and Prometheus output."""

    INFORMATIONAL = "1xx"
    SUCCESS = "2xx"
    REDIRECTION = "3xx"
    CLIENT_ERROR = "4xx"
    SERVER_ERROR = "5xx"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class HttpStatusCountSnapshot:
    status_class: HttpStatusClassName
    count: int


@dataclass(frozen=True, slots=True)
class HttpDurationBucketSnapshot:
    upper_bound_seconds: float
    count: int


@dataclass(frozen=True, slots=True)
class HttpRouteMetricsSnapshot:
    method: str
    route: str
    requests: int
    status_counts: tuple[HttpStatusCountSnapshot, ...]
    duration_buckets: tuple[HttpDurationBucketSnapshot, ...]
    duration_sum_seconds: float
    average_duration_seconds: float
    max_duration_seconds: float


@dataclass(frozen=True, slots=True)
class HttpMetricsSnapshot:
    requests_total: int
    in_flight: int
    peak_in_flight: int
    max_route_keys: int
    tracked_route_keys: int
    route_overflow_requests: int
    recording_errors: int
    duration_buckets_seconds: tuple[float, ...]
    routes: tuple[HttpRouteMetricsSnapshot, ...]


@dataclass(slots=True)
class _MutableRouteMetrics:
    status_counts: dict[HttpStatusClassName, int] = field(default_factory=dict)
    duration_bucket_counts: list[int] = field(default_factory=list)
    requests: int = 0
    duration_sum_seconds: float = 0.0
    max_duration_seconds: float = 0.0


def _normalize_method(method: str) -> str:
    normalized = method.strip().upper()
    return normalized if normalized in _KNOWN_METHODS else "OTHER"


def _normalize_route(route: str | None) -> str:
    if route is None or not route.startswith("/") or len(route) > 200:
        return _UNMATCHED_ROUTE
    return route


def _status_class(status_code: int) -> HttpStatusClassName:
    if 100 <= status_code <= 199:
        return HttpStatusClassName.INFORMATIONAL
    if 200 <= status_code <= 299:
        return HttpStatusClassName.SUCCESS
    if 300 <= status_code <= 399:
        return HttpStatusClassName.REDIRECTION
    if 400 <= status_code <= 499:
        return HttpStatusClassName.CLIENT_ERROR
    if 500 <= status_code <= 599:
        return HttpStatusClassName.SERVER_ERROR
    return HttpStatusClassName.OTHER


class HttpMetricsRegistry:
    """Thread-safe, bounded-cardinality HTTP counters and latency histograms."""

    def __init__(
        self,
        *,
        max_route_keys: int = 64,
        duration_buckets_seconds: tuple[float, ...] = (DEFAULT_DURATION_BUCKETS_SECONDS),
    ) -> None:
        if max_route_keys < 1:
            raise ValueError("max_route_keys must be at least one")
        if not duration_buckets_seconds:
            raise ValueError("duration_buckets_seconds must not be empty")
        previous = 0.0
        for bucket in duration_buckets_seconds:
            if not math.isfinite(bucket) or bucket <= previous:
                raise ValueError(
                    "duration buckets must be finite, positive, and strictly increasing"
                )
            previous = bucket

        self._max_route_keys = max_route_keys
        self._duration_buckets_seconds = duration_buckets_seconds
        self._lock = threading.Lock()
        self._routes: dict[tuple[str, str], _MutableRouteMetrics] = {}
        self._normal_route_keys: set[tuple[str, str]] = set()
        self._requests_total = 0
        self._in_flight = 0
        self._peak_in_flight = 0
        self._route_overflow_requests = 0
        self._recording_errors = 0

    def request_started(self) -> None:
        with self._lock:
            self._in_flight += 1
            self._peak_in_flight = max(self._peak_in_flight, self._in_flight)

    def request_finished(
        self,
        *,
        method: str,
        route: str | None,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        normalized_method = _normalize_method(method)
        normalized_route = _normalize_route(route)
        if not math.isfinite(duration_seconds) or duration_seconds < 0:
            normalized_duration = 0.0
            invalid_duration = True
        else:
            normalized_duration = duration_seconds
            invalid_duration = False

        with self._lock:
            if self._in_flight > 0:
                self._in_flight -= 1
            else:
                self._recording_errors += 1
            if invalid_duration:
                self._recording_errors += 1

            normal_key = (normalized_method, normalized_route)
            if (
                normal_key not in self._normal_route_keys
                and len(self._normal_route_keys) >= self._max_route_keys
            ):
                key = (normalized_method, _OVERFLOW_ROUTE)
                self._route_overflow_requests += 1
            else:
                key = normal_key
                self._normal_route_keys.add(normal_key)

            route_metrics = self._routes.get(key)
            if route_metrics is None:
                route_metrics = _MutableRouteMetrics(
                    duration_bucket_counts=[0 for _ in self._duration_buckets_seconds]
                )
                self._routes[key] = route_metrics

            status_class = _status_class(status_code)
            route_metrics.requests += 1
            route_metrics.status_counts[status_class] = (
                route_metrics.status_counts.get(status_class, 0) + 1
            )
            route_metrics.duration_sum_seconds += normalized_duration
            route_metrics.max_duration_seconds = max(
                route_metrics.max_duration_seconds,
                normalized_duration,
            )
            for index, upper_bound in enumerate(self._duration_buckets_seconds):
                if normalized_duration <= upper_bound:
                    route_metrics.duration_bucket_counts[index] += 1
            self._requests_total += 1

    def snapshot(self) -> HttpMetricsSnapshot:
        with self._lock:
            routes: list[HttpRouteMetricsSnapshot] = []
            for (method, route), metrics in sorted(self._routes.items()):
                status_counts = tuple(
                    HttpStatusCountSnapshot(
                        status_class=status_class,
                        count=count,
                    )
                    for status_class, count in sorted(
                        metrics.status_counts.items(),
                        key=lambda item: item[0].value,
                    )
                )
                buckets = tuple(
                    HttpDurationBucketSnapshot(
                        upper_bound_seconds=upper_bound,
                        count=metrics.duration_bucket_counts[index],
                    )
                    for index, upper_bound in enumerate(self._duration_buckets_seconds)
                )
                average = (
                    metrics.duration_sum_seconds / metrics.requests if metrics.requests else 0.0
                )
                routes.append(
                    HttpRouteMetricsSnapshot(
                        method=method,
                        route=route,
                        requests=metrics.requests,
                        status_counts=status_counts,
                        duration_buckets=buckets,
                        duration_sum_seconds=metrics.duration_sum_seconds,
                        average_duration_seconds=average,
                        max_duration_seconds=metrics.max_duration_seconds,
                    )
                )

            return HttpMetricsSnapshot(
                requests_total=self._requests_total,
                in_flight=self._in_flight,
                peak_in_flight=self._peak_in_flight,
                max_route_keys=self._max_route_keys,
                tracked_route_keys=len(self._normal_route_keys),
                route_overflow_requests=self._route_overflow_requests,
                recording_errors=self._recording_errors,
                duration_buckets_seconds=self._duration_buckets_seconds,
                routes=tuple(routes),
            )
