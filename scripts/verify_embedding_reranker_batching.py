from __future__ import annotations

import json
import math

from app.performance import BatchOptimizationScenarioName
from scripts.run_batch_optimization import run_comparison

_ITEM_COUNT = 32
_BATCH_SIZE = 8
_CALL_OVERHEAD_MS = 1.5
_BATCH_LATENCY_MS = 0.25


def run_verification() -> dict[str, object]:
    """Exercise the Day 26 Embedding/Reranker batching contract offline."""

    report = run_comparison(
        item_count=_ITEM_COUNT,
        batch_size=_BATCH_SIZE,
        call_overhead_ms=_CALL_OVERHEAD_MS,
        batch_latency_ms=_BATCH_LATENCY_MS,
    )
    results = {result.scenario: result for result in report.scenario_results}
    expected_batches = math.ceil(_ITEM_COUNT / _BATCH_SIZE)
    checks = {
        "embedding_and_reranker_covered": (set(results) == set(BatchOptimizationScenarioName)),
        "sequential_calls_once_per_item": all(
            result.sequential_provider_calls == _ITEM_COUNT for result in results.values()
        ),
        "batch_path_uses_one_provider_call": all(
            result.batched_provider_calls == 1 for result in results.values()
        ),
        "internal_batches_match_batch_size": all(
            result.sequential_internal_batches == _ITEM_COUNT
            and result.batched_internal_batches == expected_batches
            for result in results.values()
        ),
        "outputs_are_exactly_equivalent": all(
            result.outputs_equivalent for result in results.values()
        ),
        "output_order_is_preserved": all(result.order_preserved for result in results.values()),
        "provider_calls_are_reduced": all(
            result.provider_call_reduction == 1 - (1 / _ITEM_COUNT) for result in results.values()
        ),
        "offline_fixture_observed_speedup": all(
            result.batched_faster and result.throughput_speedup > 1.0 for result in results.values()
        ),
        "offline_boundary_preserved": (
            report.network_calls is False and report.live_model_calls is False
        ),
        "quality_gate_passed": report.quality_gate_passed,
    }
    return {
        "passed": all(checks.values()),
        "suite_name": report.suite_name,
        "schema_version": report.schema_version,
        "item_count": report.item_count,
        "configured_batch_size": report.configured_batch_size,
        "network_calls": report.network_calls,
        "live_model_calls": report.live_model_calls,
        "decision": report.decision,
        "scenarios": {
            result.scenario.value: {
                "sequential_provider_calls": result.sequential_provider_calls,
                "batched_provider_calls": result.batched_provider_calls,
                "sequential_internal_batches": result.sequential_internal_batches,
                "batched_internal_batches": result.batched_internal_batches,
                "provider_call_reduction": round(result.provider_call_reduction, 6),
                "throughput_speedup": round(result.throughput_speedup, 3),
                "outputs_equivalent": result.outputs_equivalent,
                "order_preserved": result.order_preserved,
            }
            for result in report.scenario_results
        },
        "checks": checks,
    }


def main() -> int:
    report = run_verification()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
