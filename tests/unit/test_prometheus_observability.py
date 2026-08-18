from __future__ import annotations

from app.llm import (
    ProviderLimiterMetricsSnapshot,
    ProviderLimiterStateName,
    ProviderLimiterStatus,
)
from app.observability import HttpMetricsRegistry, render_prometheus_metrics


def _provider_status() -> ProviderLimiterStatus:
    return ProviderLimiterStatus(
        enabled=True,
        state=ProviderLimiterStateName.QUEUING,
        max_concurrency=4,
        max_queue=16,
        queue_timeout_seconds=2.0,
        in_flight=4,
        queued=2,
        metrics=ProviderLimiterMetricsSnapshot(
            requests=12,
            bypassed=0,
            accepted=11,
            started=8,
            completed=3,
            failed=1,
            rejected=1,
            timed_out=1,
            cancelled=0,
            peak_in_flight=4,
            peak_queued=5,
            average_wait_ms=12.0,
        ),
    )


def test_renders_histogram_status_and_provider_metrics() -> None:
    registry = HttpMetricsRegistry(duration_buckets_seconds=(0.01, 0.1))
    registry.request_started()
    registry.request_finished(
        method="GET",
        route='/items/{item_id}/say"hello',
        status_code=200,
        duration_seconds=0.02,
    )

    output = render_prometheus_metrics(
        registry.snapshot(),
        provider=_provider_status(),
    )

    assert "# TYPE enterprise_policy_agent_http_requests_total counter" in output
    assert 'method="GET",route="/items/{item_id}/say\\"hello",status_class="2xx"} 1' in output
    assert 'le="0.01"} 0' in output
    assert 'le="0.1"} 1' in output
    assert 'le="+Inf"} 1' in output
    assert "enterprise_policy_agent_llm_provider_limiter_available 1" in output
    assert 'enterprise_policy_agent_llm_provider_limiter_state{state="queuing"} 1' in output
    assert 'enterprise_policy_agent_llm_provider_events_total{event="rejected"} 1' in output
    assert output.endswith("\n")


def test_renders_unavailable_provider_without_fabricating_values() -> None:
    output = render_prometheus_metrics(
        HttpMetricsRegistry().snapshot(),
        provider=None,
    )

    assert "enterprise_policy_agent_llm_provider_limiter_available 0" in output
    assert "enterprise_policy_agent_llm_provider_in_flight" not in output
