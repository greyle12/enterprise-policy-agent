from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.performance import (
    ConcurrencyLoadRunner,
    ConcurrencyLoadScenario,
    ConcurrencyLoadScenarioName,
    ConcurrencyObservedMetrics,
)


async def _no_op(_request_index: int) -> object:
    return None


async def _empty_metrics() -> ConcurrencyObservedMetrics:
    return ConcurrencyObservedMetrics(
        upstream_calls=1,
        provider_peak_in_flight=1,
    )


async def _close() -> None:
    return None


def _scenario(**overrides: object) -> ConcurrencyLoadScenario:
    values: dict[str, object] = {
        "name": ConcurrencyLoadScenarioName.HOT_KEY_BURST,
        "description": "test load scenario",
        "request_count": 1,
        "concurrency": 1,
        "unique_request_keys": 1,
        "expected_upstream_calls": 1,
        "operation": _no_op,
        "observe": _empty_metrics,
        "close": _close,
    }
    values.update(overrides)
    return ConcurrencyLoadScenario(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("request_count", 0),
        ("concurrency", 0),
        ("unique_request_keys", 0),
        ("expected_upstream_calls", 0),
        ("description", "  "),
    ],
)
def test_scenario_rejects_invalid_shape(field_name: str, invalid_value: object) -> None:
    with pytest.raises(ValueError):
        _scenario(**{field_name: invalid_value})


def test_scenario_rejects_more_unique_keys_than_requests() -> None:
    with pytest.raises(ValueError, match="must not exceed"):
        _scenario(request_count=2, unique_request_keys=3)


async def test_runner_bounds_concurrency_and_collects_load_metrics() -> None:
    active = 0
    peak_active = 0
    calls = 0
    closed = False

    async def operation(_request_index: int) -> object:
        nonlocal active, peak_active, calls
        calls += 1
        active += 1
        peak_active = max(peak_active, active)
        try:
            await asyncio.sleep(0.005)
        finally:
            active -= 1
        return None

    async def observe() -> ConcurrencyObservedMetrics:
        return ConcurrencyObservedMetrics(
            upstream_calls=calls,
            provider_peak_in_flight=peak_active,
        )

    async def close() -> None:
        nonlocal closed
        closed = True

    runner = ConcurrencyLoadRunner(
        scenarios=(
            _scenario(
                request_count=6,
                concurrency=2,
                unique_request_keys=6,
                expected_upstream_calls=6,
                operation=operation,
                observe=observe,
                close=close,
            ),
        ),
        simulated_provider_latency_ms=5.0,
        generated_at=lambda: datetime(2026, 8, 15, tzinfo=UTC),
    )

    report = await runner.run()
    result = report.scenario_results[0]

    assert report.generated_at == datetime(2026, 8, 15, tzinfo=UTC)
    assert report.quality_gate_passed is True
    assert result.client_peak_in_flight == 2
    assert result.provider_peak_in_flight == 2
    assert result.upstream_calls == 6
    assert result.upstream_call_ratio == 1.0
    assert result.upstream_call_amplification == 1.0
    assert result.error_rate == 0.0
    assert result.p95_ms >= result.p50_ms > 0
    assert result.throughput_rps > 0
    assert closed is True


async def test_runner_records_stable_error_type_and_fails_contract() -> None:
    calls = 0

    async def operation(request_index: int) -> object:
        nonlocal calls
        calls += 1
        if request_index == 1:
            raise ValueError("secret failure text")
        return None

    async def observe() -> ConcurrencyObservedMetrics:
        return ConcurrencyObservedMetrics(
            upstream_calls=calls,
            provider_peak_in_flight=1,
        )

    runner = ConcurrencyLoadRunner(
        scenarios=(
            _scenario(
                request_count=3,
                concurrency=1,
                unique_request_keys=3,
                expected_upstream_calls=3,
                operation=operation,
                observe=observe,
            ),
        ),
        simulated_provider_latency_ms=1.0,
    )

    report = await runner.run()
    result = report.scenario_results[0]

    assert report.quality_gate_passed is False
    assert result.error_count == 1
    assert result.error_rate == pytest.approx(1 / 3)
    assert result.meets_contract is False
    assert result.samples[1].error_type == "ValueError"
    assert "secret failure text" not in result.model_dump_json()


def test_runner_rejects_duplicate_scenarios_and_invalid_latency() -> None:
    scenario = _scenario()
    with pytest.raises(ValueError, match="unique"):
        ConcurrencyLoadRunner(
            scenarios=(scenario, scenario),
            simulated_provider_latency_ms=1.0,
        )
    with pytest.raises(ValueError, match="latency"):
        ConcurrencyLoadRunner(
            scenarios=(scenario,),
            simulated_provider_latency_ms=0.0,
        )
