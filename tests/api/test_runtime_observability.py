from __future__ import annotations

import re

from fastapi import Request
from fastapi.testclient import TestClient

from app.llm import (
    ProviderLimiterMetricsSnapshot,
    ProviderLimiterStateName,
    ProviderLimiterStatus,
)
from app.main import create_app


class FakeProviderLimiter:
    async def status(self) -> ProviderLimiterStatus:
        return ProviderLimiterStatus(
            enabled=True,
            state=ProviderLimiterStateName.AVAILABLE,
            max_concurrency=4,
            max_queue=16,
            queue_timeout_seconds=2.0,
            in_flight=1,
            queued=0,
            metrics=ProviderLimiterMetricsSnapshot(
                requests=3,
                bypassed=0,
                accepted=3,
                started=3,
                completed=2,
                failed=0,
                rejected=0,
                timed_out=0,
                cancelled=0,
                peak_in_flight=2,
                peak_queued=0,
                average_wait_ms=1.25,
            ),
        )


def _probe_application(*, max_route_keys: int = 64):
    application = create_app(
        enable_lifespan=False,
        http_metrics_max_route_keys=max_route_keys,
    )

    @application.get("/probe/{item_id}")
    async def probe(item_id: str, request: Request) -> dict[str, str]:
        del item_id
        return {"request_id": request.state.request_id}

    @application.get("/explode/{item_id}")
    async def explode(item_id: str) -> None:
        raise RuntimeError(f"private failure for {item_id}")

    return application


def test_request_id_is_available_to_handler_and_response() -> None:
    application = _probe_application()

    with TestClient(application) as client:
        response = client.get(
            "/probe/customer-927?api_key=do-not-record",
            headers={"X-Request-ID": "client-request-123"},
        )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "client-request-123"
    assert response.json() == {"request_id": "client-request-123"}


def test_unsafe_request_id_is_replaced() -> None:
    application = _probe_application()

    with TestClient(application) as client:
        response = client.get(
            "/probe/example",
            headers={"X-Request-ID": "unsafe request id"},
        )

    generated = response.headers["x-request-id"]
    assert re.fullmatch(r"req_[0-9a-f]{32}", generated)
    assert response.json()["request_id"] == generated


def test_status_uses_route_templates_and_hides_raw_request_data() -> None:
    application = _probe_application()
    secret = "customer-account-927"

    with TestClient(application) as client:
        client.get(f"/probe/{secret}?api_key=super-secret")
        client.get(f"/not-found/{secret}?token=another-secret")
        first = client.get("/api/v1/observability/status")
        second = client.get("/api/v1/observability/status")

    assert first.status_code == 200
    assert first.json() == second.json()
    payload = first.json()
    assert payload["requests_total"] == 2
    assert payload["in_flight"] == 0
    assert payload["tracked_route_keys"] == 2
    assert {item["route"] for item in payload["routes"]} == {
        "/probe/{item_id}",
        "__unmatched__",
    }
    serialized = first.text
    assert secret not in serialized
    assert "super-secret" not in serialized
    assert "another-secret" not in serialized
    assert "api_key" not in serialized


def test_prometheus_endpoint_is_safe_and_does_not_measure_itself() -> None:
    application = _probe_application()
    application.state.llm_provider_limiter = FakeProviderLimiter()

    with TestClient(application) as client:
        client.get("/probe/private-value?token=private-token")
        first = client.get("/metrics")
        second = client.get("/metrics")

    assert first.status_code == 200
    assert first.headers["content-type"] == ("text/plain; version=0.0.4; charset=utf-8")
    assert first.headers["cache-control"] == "no-store"
    assert first.text == second.text
    assert 'route="/probe/{item_id}"' in first.text
    assert "enterprise_policy_agent_llm_provider_limiter_available 1" in first.text
    assert "enterprise_policy_agent_llm_provider_in_flight 1" in first.text
    assert "private-value" not in first.text
    assert "private-token" not in first.text


def test_unhandled_error_is_correlated_sanitized_and_measured() -> None:
    application = _probe_application()
    secret = "api_key-private-927"

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get(
            f"/explode/{secret}",
            headers={"X-Request-ID": "failure-request-123"},
        )
        status_response = client.get("/api/v1/observability/status")

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "failure-request-123"
    assert response.json() == {
        "detail": {
            "code": "internal_server_error",
            "message": "The request could not be completed.",
        },
        "request_id": "failure-request-123",
    }
    assert secret not in response.text
    route = status_response.json()["routes"][0]
    assert route["route"] == "/explode/{item_id}"
    assert route["status_counts"] == [{"status_class": "5xx", "count": 1}]


def test_route_registry_maps_excess_templates_to_overflow() -> None:
    application = create_app(
        enable_lifespan=False,
        http_metrics_max_route_keys=1,
    )

    @application.get("/first")
    async def first() -> dict[str, bool]:
        return {"ok": True}

    @application.get("/second")
    async def second() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(application) as client:
        client.get("/first")
        client.get("/second")
        response = client.get("/api/v1/observability/status")

    payload = response.json()
    assert payload["tracked_route_keys"] == 1
    assert payload["route_overflow_requests"] == 1
    assert {item["route"] for item in payload["routes"]} == {
        "/first",
        "__overflow__",
    }


def test_openapi_exposes_status_but_hides_scrape_endpoint() -> None:
    schema = create_app(enable_lifespan=False).openapi()

    assert "/api/v1/observability/status" in schema["paths"]
    assert "/metrics" not in schema["paths"]
    assert "RuntimeObservabilityStatusResponse" in schema["components"]["schemas"]
