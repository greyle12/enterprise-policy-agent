from __future__ import annotations

import asyncio
import json

from app.performance import ConcurrencyLoadScenarioName
from scripts.run_concurrency_load_test import run_load

_REQUEST_COUNT = 24
_CONCURRENCY = 12
_PROVIDER_LATENCY_MS = 15.0


async def _run_verification() -> dict[str, object]:
    report = await run_load(
        request_count=_REQUEST_COUNT,
        concurrency=_CONCURRENCY,
        provider_latency_ms=_PROVIDER_LATENCY_MS,
    )
    results = {result.scenario: result for result in report.scenario_results}
    hot = results[ConcurrencyLoadScenarioName.HOT_KEY_BURST]
    mixed = results[ConcurrencyLoadScenarioName.MIXED_HOTSET]
    unique = results[ConcurrencyLoadScenarioName.UNIQUE_KEY_FANOUT]

    checks = {
        "three_request_distributions_covered": (set(results) == set(ConcurrencyLoadScenarioName)),
        "all_requests_accounted_for": all(
            len(result.samples) == _REQUEST_COUNT for result in results.values()
        ),
        "all_scenarios_succeeded": all(
            result.error_count == 0 and result.error_rate == 0.0 for result in results.values()
        ),
        "hot_key_uses_one_upstream_call": (
            hot.unique_request_keys == 1
            and hot.upstream_calls == 1
            and hot.coalesced_requests >= _CONCURRENCY - 1
        ),
        "mixed_hotset_merges_per_key": (
            mixed.unique_request_keys == 4
            and mixed.upstream_calls == 4
            and mixed.coalesced_requests >= _CONCURRENCY - 4
        ),
        "unique_keys_show_full_fanout": (
            unique.unique_request_keys == _REQUEST_COUNT
            and unique.upstream_calls == _REQUEST_COUNT
            and unique.provider_peak_in_flight == _CONCURRENCY
        ),
        "no_upstream_amplification": all(
            result.upstream_call_amplification == 1.0 for result in results.values()
        ),
        "latency_and_throughput_measured": all(
            result.p95_ms > 0 and result.throughput_rps > 0 for result in results.values()
        ),
        "client_concurrency_was_bounded": all(
            result.client_peak_in_flight <= _CONCURRENCY for result in results.values()
        ),
        "offline_boundary_preserved": (
            report.network_calls is False and report.live_llm_calls is False
        ),
        "quality_gate_passed": report.quality_gate_passed,
    }
    return {
        "passed": all(checks.values()),
        "suite_name": report.suite_name,
        "schema_version": report.schema_version,
        "request_count": report.request_count,
        "configured_concurrency": report.configured_concurrency,
        "network_calls": report.network_calls,
        "live_llm_calls": report.live_llm_calls,
        "decision": report.decision,
        "scenarios": {
            result.scenario.value: {
                "p95_ms": round(result.p95_ms, 3),
                "throughput_rps": round(result.throughput_rps, 3),
                "upstream_calls": result.upstream_calls,
                "provider_peak_in_flight": result.provider_peak_in_flight,
                "error_rate": result.error_rate,
            }
            for result in report.scenario_results
        },
        "checks": checks,
    }


def run_verification() -> dict[str, object]:
    """Exercise the Day 25 concurrency load contract entirely offline."""

    return asyncio.run(_run_verification())


def main() -> int:
    report = run_verification()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
