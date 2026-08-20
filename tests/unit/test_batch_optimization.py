from datetime import UTC, datetime

import pytest

from app.performance import (
    BatchExecutionObservation,
    BatchOptimizationRunner,
    BatchOptimizationScenario,
    BatchOptimizationScenarioName,
)


def _observation(
    *,
    digest: str = "same",
    calls: int = 1,
    batches: int = 1,
) -> BatchExecutionObservation:
    return BatchExecutionObservation(
        output_digest=digest,
        output_order=("one", "two"),
        provider_calls=calls,
        internal_batches=batches,
    )


def _scenario(**overrides: object) -> BatchOptimizationScenario:
    values: dict[str, object] = {
        "name": BatchOptimizationScenarioName.EMBEDDING_DOCUMENTS,
        "description": "test batch scenario",
        "item_count": 2,
        "batch_size": 2,
        "expected_sequential_provider_calls": 2,
        "expected_batched_provider_calls": 1,
        "expected_sequential_internal_batches": 2,
        "expected_batched_internal_batches": 1,
        "run_sequential": lambda: _observation(calls=2, batches=2),
        "run_batched": lambda: _observation(),
    }
    values.update(overrides)
    return BatchOptimizationScenario(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("description", " "),
        ("item_count", 0),
        ("batch_size", 0),
        ("expected_sequential_provider_calls", 0),
        ("expected_batched_provider_calls", 0),
        ("expected_sequential_internal_batches", 0),
        ("expected_batched_internal_batches", 0),
    ],
)
def test_scenario_rejects_invalid_shape(field_name: str, value: object) -> None:
    with pytest.raises(ValueError):
        _scenario(**{field_name: value})


def test_observation_rejects_blank_digest_and_non_positive_counters() -> None:
    with pytest.raises(ValueError, match="digest"):
        BatchExecutionObservation(" ", (), 0, 0)
    with pytest.raises(ValueError, match="counters"):
        BatchExecutionObservation("digest", (), 0, 1)


def test_runner_reports_equivalence_call_reduction_and_throughput() -> None:
    ticks = iter([0, 0, 2_000_000, 2_000_000, 3_000_000, 3_000_000])
    runner = BatchOptimizationRunner(
        scenarios=(_scenario(),),
        simulated_call_overhead_ms=1.5,
        simulated_batch_latency_ms=0.25,
        monotonic_ns=lambda: next(ticks),
        generated_at=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )

    report = runner.run()
    result = report.scenario_results[0]

    assert report.generated_at == datetime(2026, 8, 17, tzinfo=UTC)
    assert report.quality_gate_passed is True
    assert result.sequential_duration_ms == 2.0
    assert result.batched_duration_ms == 1.0
    assert result.provider_call_reduction == 0.5
    assert result.throughput_speedup == 2.0
    assert result.outputs_equivalent is True
    assert result.order_preserved is True
    assert result.batched_faster is True
    assert result.meets_contract is True


def test_runner_fails_contract_when_outputs_differ() -> None:
    runner = BatchOptimizationRunner(
        scenarios=(_scenario(run_batched=lambda: _observation(digest="different")),),
        simulated_call_overhead_ms=1.0,
        simulated_batch_latency_ms=1.0,
    )

    report = runner.run()

    assert report.quality_gate_passed is False
    assert report.scenario_results[0].outputs_equivalent is False
    assert report.scenario_results[0].meets_contract is False


def test_runner_rejects_duplicate_names_mixed_shapes_and_invalid_latency() -> None:
    scenario = _scenario()
    with pytest.raises(ValueError, match="unique"):
        BatchOptimizationRunner(
            scenarios=(scenario, scenario),
            simulated_call_overhead_ms=1.0,
            simulated_batch_latency_ms=1.0,
        )
    with pytest.raises(ValueError, match="same batch shape"):
        BatchOptimizationRunner(
            scenarios=(
                scenario,
                _scenario(
                    name=BatchOptimizationScenarioName.RERANKER_CANDIDATES,
                    item_count=3,
                ),
            ),
            simulated_call_overhead_ms=1.0,
            simulated_batch_latency_ms=1.0,
        )
    with pytest.raises(ValueError, match="call_overhead"):
        BatchOptimizationRunner(
            scenarios=(scenario,),
            simulated_call_overhead_ms=0.0,
            simulated_batch_latency_ms=1.0,
        )
