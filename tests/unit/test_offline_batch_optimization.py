import math

import pytest

from app.performance import BatchOptimizationScenarioName, run_offline_batch_optimization


def test_offline_batch_optimization_preserves_results_and_reduces_calls() -> None:
    report = run_offline_batch_optimization(
        item_count=16,
        batch_size=4,
        call_overhead_ms=0.5,
        batch_latency_ms=0.1,
    )

    assert report.quality_gate_passed is True
    assert report.network_calls is False
    assert report.live_model_calls is False
    assert {result.scenario for result in report.scenario_results} == set(
        BatchOptimizationScenarioName
    )
    for result in report.scenario_results:
        assert result.sequential_provider_calls == 16
        assert result.batched_provider_calls == 1
        assert result.provider_call_reduction == pytest.approx(15 / 16)
        assert result.sequential_internal_batches == 16
        assert result.batched_internal_batches == math.ceil(16 / 4)
        assert result.outputs_equivalent is True
        assert result.order_preserved is True
        assert result.batched_faster is True
        assert result.throughput_speedup > 1.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"item_count": 0},
        {"batch_size": 0},
        {"call_overhead_ms": 0.0},
        {"batch_latency_ms": 0.0},
    ],
)
def test_offline_batch_optimization_rejects_invalid_inputs(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        run_offline_batch_optimization(**overrides)  # type: ignore[arg-type]
