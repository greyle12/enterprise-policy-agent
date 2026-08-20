from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.observability import HttpMetricsRegistry, HttpStatusClassName


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_route_keys": 0},
        {"duration_buckets_seconds": ()},
        {"duration_buckets_seconds": (0.1, 0.1)},
        {"duration_buckets_seconds": (0.1, float("inf"))},
    ],
)
def test_registry_rejects_invalid_bounds(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        HttpMetricsRegistry(**kwargs)


def test_records_status_latency_and_peak_concurrency() -> None:
    registry = HttpMetricsRegistry(duration_buckets_seconds=(0.01, 0.1, 1.0))
    registry.request_started()
    registry.request_started()
    registry.request_finished(
        method="get",
        route="/items/{item_id}",
        status_code=200,
        duration_seconds=0.008,
    )
    registry.request_finished(
        method="GET",
        route="/items/{item_id}",
        status_code=503,
        duration_seconds=0.2,
    )

    snapshot = registry.snapshot()

    assert snapshot.requests_total == 2
    assert snapshot.in_flight == 0
    assert snapshot.peak_in_flight == 2
    assert snapshot.tracked_route_keys == 1
    route = snapshot.routes[0]
    assert route.method == "GET"
    assert route.route == "/items/{item_id}"
    assert route.requests == 2
    assert {item.status_class: item.count for item in route.status_counts} == {
        HttpStatusClassName.SUCCESS: 1,
        HttpStatusClassName.SERVER_ERROR: 1,
    }
    assert [bucket.count for bucket in route.duration_buckets] == [1, 1, 2]
    assert route.duration_sum_seconds == pytest.approx(0.208)
    assert route.average_duration_seconds == pytest.approx(0.104)
    assert route.max_duration_seconds == pytest.approx(0.2)


def test_bounds_route_cardinality_with_overflow_label() -> None:
    registry = HttpMetricsRegistry(max_route_keys=1)

    for route in ("/first", "/second", "/second"):
        registry.request_started()
        registry.request_finished(
            method="GET",
            route=route,
            status_code=200,
            duration_seconds=0.01,
        )

    snapshot = registry.snapshot()

    assert snapshot.requests_total == 3
    assert snapshot.tracked_route_keys == 1
    assert snapshot.route_overflow_requests == 2
    assert [(item.route, item.requests) for item in snapshot.routes] == [
        ("/first", 1),
        ("__overflow__", 2),
    ]


def test_normalizes_unknown_method_route_and_invalid_observation() -> None:
    registry = HttpMetricsRegistry()
    registry.request_finished(
        method="TRACE-WITH-SECRET",
        route="raw-user-path",
        status_code=799,
        duration_seconds=float("nan"),
    )

    snapshot = registry.snapshot()

    assert snapshot.recording_errors == 2
    assert snapshot.routes[0].method == "OTHER"
    assert snapshot.routes[0].route == "__unmatched__"
    assert snapshot.routes[0].status_counts[0].status_class is HttpStatusClassName.OTHER
    assert snapshot.routes[0].duration_sum_seconds == 0


def test_registry_is_thread_safe_for_short_updates() -> None:
    registry = HttpMetricsRegistry()

    def record(index: int) -> None:
        registry.request_started()
        registry.request_finished(
            method="POST",
            route="/work/{item_id}",
            status_code=200 if index % 2 == 0 else 400,
            duration_seconds=0.01,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        tuple(executor.map(record, range(200)))

    snapshot = registry.snapshot()
    assert snapshot.requests_total == 200
    assert snapshot.in_flight == 0
    assert snapshot.recording_errors == 0
    assert snapshot.routes[0].requests == 200
