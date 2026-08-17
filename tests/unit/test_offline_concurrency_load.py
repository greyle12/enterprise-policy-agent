from __future__ import annotations

from app.performance import (
    ConcurrencyLoadScenarioName,
    run_offline_concurrency_load,
)


async def test_offline_load_covers_hot_mixed_and_unique_request_distributions() -> None:
    report = await run_offline_concurrency_load(
        request_count=24,
        concurrency=12,
        provider_latency_ms=5.0,
    )
    results = {result.scenario: result for result in report.scenario_results}

    assert report.quality_gate_passed is True
    assert report.network_calls is False
    assert report.live_llm_calls is False
    assert set(results) == set(ConcurrencyLoadScenarioName)

    hot = results[ConcurrencyLoadScenarioName.HOT_KEY_BURST]
    assert hot.unique_request_keys == 1
    assert hot.upstream_calls == 1
    assert hot.provider_peak_in_flight == 1
    assert hot.coalesced_requests >= 11

    mixed = results[ConcurrencyLoadScenarioName.MIXED_HOTSET]
    assert mixed.unique_request_keys == 4
    assert mixed.upstream_calls == 4
    assert mixed.provider_peak_in_flight == 4
    assert mixed.coalesced_requests >= 8

    unique = results[ConcurrencyLoadScenarioName.UNIQUE_KEY_FANOUT]
    assert unique.unique_request_keys == 24
    assert unique.upstream_calls == 24
    assert unique.provider_peak_in_flight == 12
    assert unique.coalesced_requests == 0

    assert all(result.error_rate == 0 for result in results.values())
    assert all(result.upstream_call_amplification == 1.0 for result in results.values())


async def test_offline_load_supports_concurrency_larger_than_request_count() -> None:
    report = await run_offline_concurrency_load(
        request_count=3,
        concurrency=8,
        provider_latency_ms=1.0,
    )
    unique = next(
        result
        for result in report.scenario_results
        if result.scenario is ConcurrencyLoadScenarioName.UNIQUE_KEY_FANOUT
    )

    assert report.quality_gate_passed is True
    assert unique.client_peak_in_flight == 3
    assert unique.provider_peak_in_flight == 3
