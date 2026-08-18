from __future__ import annotations

import json
import logging
import re

from fastapi import Request
from fastapi.testclient import TestClient

from app.main import create_app
from app.observability import JsonLogFormatter


def _formatted_access_log() -> str:
    record = logging.Logger("offline.observability").makeRecord(
        name="offline.observability",
        level=logging.INFO,
        fn=__file__,
        lno=1,
        msg="http_request_completed",
        args=(),
        exc_info=None,
        extra={
            "request_id": "offline-request-001",
            "method": "GET",
            "route": "/offline/{item_id}",
            "status_code": 200,
            "duration_ms": 1.25,
            "outcome": "success",
            "raw_query": "api_key=log-secret",
        },
    )
    return JsonLogFormatter().format(record)


def run_verification() -> dict[str, object]:
    """Exercise Day 28 request correlation and telemetry entirely in-process."""

    application = create_app(
        enable_lifespan=False,
        http_metrics_max_route_keys=2,
    )

    @application.get("/offline/{item_id}")
    async def offline_probe(item_id: str, request: Request) -> dict[str, str]:
        del item_id
        return {"request_id": request.state.request_id}

    @application.get("/offline-error/{item_id}")
    async def offline_error(item_id: str) -> None:
        raise RuntimeError(f"private upstream detail for {item_id}")

    secret_item = "employee-secret-927"
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(application, raise_server_exceptions=False) as client:
            correlated = client.get(
                f"/offline/{secret_item}?api_key=request-secret",
                headers={"X-Request-ID": "offline-request-001"},
            )
            generated = client.get(
                "/offline/generated",
                headers={"X-Request-ID": "unsafe request id"},
            )
            failed = client.get(
                f"/offline-error/{secret_item}",
                headers={"X-Request-ID": "offline-failure-001"},
            )
            status_response = client.get("/api/v1/observability/status")
            status_again = client.get("/api/v1/observability/status")
            prometheus = client.get("/metrics")
            prometheus_again = client.get("/metrics")
    finally:
        logging.disable(previous_logging_disable)

    status_payload = status_response.json()
    serialized_evidence = "\n".join(
        (
            status_response.text,
            prometheus.text,
            failed.text,
            _formatted_access_log(),
        )
    )
    routes = {item["route"]: item for item in status_payload["routes"]}
    error_statuses = {
        item["status_class"]: item["count"]
        for item in routes["/offline-error/{item_id}"]["status_counts"]
    }

    checks = {
        "safe_client_request_id_is_correlated": (
            correlated.status_code == 200
            and correlated.headers.get("x-request-id") == "offline-request-001"
            and correlated.json()["request_id"] == "offline-request-001"
        ),
        "unsafe_request_id_is_replaced": (
            re.fullmatch(
                r"req_[0-9a-f]{32}",
                generated.headers.get("x-request-id", ""),
            )
            is not None
        ),
        "route_templates_bound_cardinality": (
            status_payload["tracked_route_keys"] == 2
            and set(routes)
            == {
                "/offline/{item_id}",
                "/offline-error/{item_id}",
            }
        ),
        "http_success_and_failure_are_counted": (
            status_payload["requests_total"] == 3
            and routes["/offline/{item_id}"]["requests"] == 2
            and error_statuses == {"5xx": 1}
        ),
        "unhandled_error_is_safe_and_correlated": (
            failed.status_code == 500
            and failed.headers.get("x-request-id") == "offline-failure-001"
            and failed.json()["detail"]["code"] == "internal_server_error"
        ),
        "monitoring_endpoints_do_not_measure_themselves": (
            status_response.json() == status_again.json()
            and prometheus.text == prometheus_again.text
        ),
        "prometheus_contract_is_exposed": (
            prometheus.status_code == 200
            and "version=0.0.4" in prometheus.headers.get("content-type", "")
            and "enterprise_policy_agent_http_requests_total" in prometheus.text
            and 'route="/offline/{item_id}"' in prometheus.text
        ),
        "structured_access_log_is_json": (
            json.loads(_formatted_access_log())["event"] == "http_request_completed"
        ),
        "request_content_and_secrets_are_absent": all(
            secret not in serialized_evidence
            for secret in (
                secret_item,
                "request-secret",
                "log-secret",
                "api_key",
            )
        ),
        "metric_registry_has_no_internal_errors": (status_payload["recording_errors"] == 0),
    }

    return {
        "passed": all(checks.values()),
        "schema_version": status_payload["schema_version"],
        "requests_total": status_payload["requests_total"],
        "tracked_route_keys": status_payload["tracked_route_keys"],
        "prometheus_content_type": prometheus.headers.get("content-type"),
        "checks": checks,
        "network_calls": False,
        "live_llm_calls": False,
    }


def main() -> int:
    report = run_verification()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
