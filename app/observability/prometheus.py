from __future__ import annotations

from app.llm import ProviderLimiterStatus
from app.observability.metrics import HttpMetricsSnapshot

_PREFIX = "enterprise_policy_agent"


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(**values: str) -> str:
    serialized = ",".join(f'{name}="{_escape_label(value)}"' for name, value in values.items())
    return "{" + serialized + "}"


def _number(value: float) -> str:
    return format(value, ".12g")


def render_prometheus_metrics(
    http: HttpMetricsSnapshot,
    *,
    provider: ProviderLimiterStatus | None,
) -> str:
    """Render bounded process-local metrics in Prometheus text format 0.0.4."""

    lines = [
        f"# HELP {_PREFIX}_http_requests_in_flight Current measured HTTP requests.",
        f"# TYPE {_PREFIX}_http_requests_in_flight gauge",
        f"{_PREFIX}_http_requests_in_flight {http.in_flight}",
        f"# HELP {_PREFIX}_http_requests_peak Highest measured HTTP concurrency.",
        f"# TYPE {_PREFIX}_http_requests_peak gauge",
        f"{_PREFIX}_http_requests_peak {http.peak_in_flight}",
        (
            f"# HELP {_PREFIX}_http_route_overflow_requests_total Requests mapped "
            "to the bounded overflow label."
        ),
        f"# TYPE {_PREFIX}_http_route_overflow_requests_total counter",
        f"{_PREFIX}_http_route_overflow_requests_total {http.route_overflow_requests}",
        (
            f"# HELP {_PREFIX}_http_metrics_recording_errors_total Internal metric "
            "recording invariant errors."
        ),
        f"# TYPE {_PREFIX}_http_metrics_recording_errors_total counter",
        f"{_PREFIX}_http_metrics_recording_errors_total {http.recording_errors}",
        f"# HELP {_PREFIX}_http_requests_total Completed HTTP requests by route.",
        f"# TYPE {_PREFIX}_http_requests_total counter",
    ]

    for route in http.routes:
        for status in route.status_counts:
            lines.append(
                f"{_PREFIX}_http_requests_total"
                f"{_labels(method=route.method, route=route.route, status_class=status.status_class.value)} "
                f"{status.count}"
            )

    lines.extend(
        [
            (
                f"# HELP {_PREFIX}_http_request_duration_seconds HTTP request "
                "latency by normalized route."
            ),
            f"# TYPE {_PREFIX}_http_request_duration_seconds histogram",
        ]
    )
    for route in http.routes:
        base_labels = {"method": route.method, "route": route.route}
        for bucket in route.duration_buckets:
            lines.append(
                f"{_PREFIX}_http_request_duration_seconds_bucket"
                f"{_labels(**base_labels, le=_number(bucket.upper_bound_seconds))} "
                f"{bucket.count}"
            )
        lines.append(
            f"{_PREFIX}_http_request_duration_seconds_bucket"
            f"{_labels(**base_labels, le='+Inf')} {route.requests}"
        )
        lines.append(
            f"{_PREFIX}_http_request_duration_seconds_sum"
            f"{_labels(**base_labels)} {_number(route.duration_sum_seconds)}"
        )
        lines.append(
            f"{_PREFIX}_http_request_duration_seconds_count"
            f"{_labels(**base_labels)} {route.requests}"
        )

    lines.extend(
        [
            (
                f"# HELP {_PREFIX}_llm_provider_limiter_available Whether the "
                "process-local limiter is initialized."
            ),
            f"# TYPE {_PREFIX}_llm_provider_limiter_available gauge",
            f"{_PREFIX}_llm_provider_limiter_available {1 if provider else 0}",
        ]
    )
    if provider is not None:
        lines.extend(
            [
                f"# HELP {_PREFIX}_llm_provider_limiter_enabled Whether limiting is enabled.",
                f"# TYPE {_PREFIX}_llm_provider_limiter_enabled gauge",
                f"{_PREFIX}_llm_provider_limiter_enabled {int(provider.enabled)}",
                f"# HELP {_PREFIX}_llm_provider_limiter_state Current limiter state.",
                f"# TYPE {_PREFIX}_llm_provider_limiter_state gauge",
                f"{_PREFIX}_llm_provider_limiter_state{_labels(state=provider.state.value)} 1",
                f"# HELP {_PREFIX}_llm_provider_in_flight Current upstream calls.",
                f"# TYPE {_PREFIX}_llm_provider_in_flight gauge",
                f"{_PREFIX}_llm_provider_in_flight {provider.in_flight}",
                f"# HELP {_PREFIX}_llm_provider_queued Current FIFO waiters.",
                f"# TYPE {_PREFIX}_llm_provider_queued gauge",
                f"{_PREFIX}_llm_provider_queued {provider.queued}",
                f"# HELP {_PREFIX}_llm_provider_max_concurrency Configured execution limit.",
                f"# TYPE {_PREFIX}_llm_provider_max_concurrency gauge",
                f"{_PREFIX}_llm_provider_max_concurrency {provider.max_concurrency}",
                f"# HELP {_PREFIX}_llm_provider_max_queue Configured FIFO limit.",
                f"# TYPE {_PREFIX}_llm_provider_max_queue gauge",
                f"{_PREFIX}_llm_provider_max_queue {provider.max_queue}",
                f"# HELP {_PREFIX}_llm_provider_events_total Process-local limiter events.",
                f"# TYPE {_PREFIX}_llm_provider_events_total counter",
            ]
        )
        events = {
            "accepted": provider.metrics.accepted,
            "bypassed": provider.metrics.bypassed,
            "cancelled": provider.metrics.cancelled,
            "completed": provider.metrics.completed,
            "failed": provider.metrics.failed,
            "received": provider.metrics.requests,
            "rejected": provider.metrics.rejected,
            "started": provider.metrics.started,
            "timed_out": provider.metrics.timed_out,
        }
        for event, count in sorted(events.items()):
            lines.append(f"{_PREFIX}_llm_provider_events_total{_labels(event=event)} {count}")

    return "\n".join(lines) + "\n"
