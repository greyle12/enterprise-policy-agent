from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.performance import (
    BenchmarkScenario,
    BenchmarkWarmupError,
    PerformanceBenchmarkRunner,
    PerformanceBudget,
    PerformanceScenarioName,
    nearest_rank_percentile,
)


class _Clock:
    def __init__(self, values: list[int]) -> None:
        self._values = iter(values)

    def __call__(self) -> int:
        return next(self._values)


def _scenario(operation, *, max_p95_ms: float = 100.0) -> BenchmarkScenario:
    name = PerformanceScenarioName.POLICY_RAG_ANSWER
    return BenchmarkScenario(
        name=name,
        description="test scenario",
        operation=operation,
        budget=PerformanceBudget(
            scenario=name,
            max_p95_ms=max_p95_ms,
        ),
    )


def test_nearest_rank_percentile_uses_sorted_small_sample_rank() -> None:
    values = [20.0, 5.0, 15.0, 10.0]

    assert nearest_rank_percentile(values, 0.50) == 10.0
    assert nearest_rank_percentile(values, 0.95) == 20.0


@pytest.mark.parametrize("values, percentile", [([], 0.95), ([1.0], 0.0), ([1.0], 1.1)])
def test_nearest_rank_percentile_rejects_invalid_input(
    values: list[float],
    percentile: float,
) -> None:
    with pytest.raises(ValueError):
        nearest_rank_percentile(values, percentile)


async def test_runner_excludes_warmup_and_calculates_latency_summary() -> None:
    seen_iterations: list[int] = []

    async def operation(iteration: int) -> None:
        seen_iterations.append(iteration)

    runner = PerformanceBenchmarkRunner(
        scenarios=[_scenario(operation)],
        warmup_iterations=1,
        measured_iterations=3,
        monotonic_ns=_Clock(
            [
                0,
                5_000_000,
                10_000_000,
                25_000_000,
                30_000_000,
                50_000_000,
            ]
        ),
        generated_at=lambda: datetime(2026, 8, 13, tzinfo=UTC),
    )

    report = await runner.run()
    result = report.scenario_results[0]

    assert seen_iterations == [-1, 0, 1, 2]
    assert result.sample_count == 3
    assert [sample.duration_ms for sample in result.samples] == [5.0, 15.0, 20.0]
    assert result.minimum_ms == 5.0
    assert result.average_ms == pytest.approx(40 / 3)
    assert result.p50_ms == 15.0
    assert result.p95_ms == 20.0
    assert result.maximum_ms == 20.0
    assert report.duration_ms == 40.0
    assert report.quality_gate_passed is True


async def test_runner_records_only_exception_type_and_fails_error_budget() -> None:
    async def operation(iteration: int) -> None:
        if iteration == 1:
            raise RuntimeError("password=must-not-appear")

    runner = PerformanceBenchmarkRunner(
        scenarios=[_scenario(operation)],
        warmup_iterations=0,
        measured_iterations=3,
        monotonic_ns=_Clock([0, 1_000_000, 2_000_000, 3_000_000, 4_000_000, 5_000_000]),
    )

    report = await runner.run()
    result = report.scenario_results[0]
    serialized = report.model_dump_json()

    assert result.error_count == 1
    assert result.error_rate == pytest.approx(1 / 3)
    assert result.samples[1].error_type == "RuntimeError"
    assert "must-not-appear" not in serialized
    assert result.meets_budget is False
    assert report.quality_gate_passed is False


async def test_runner_stops_on_warmup_failure_without_exposing_message() -> None:
    async def operation(iteration: int) -> None:
        if iteration < 0:
            raise ConnectionError("token=warmup-secret")

    runner = PerformanceBenchmarkRunner(
        scenarios=[_scenario(operation)],
        warmup_iterations=1,
        measured_iterations=1,
    )

    with pytest.raises(BenchmarkWarmupError) as captured:
        await runner.run()

    assert captured.value.scenario is PerformanceScenarioName.POLICY_RAG_ANSWER
    assert captured.value.error_type == "ConnectionError"
    assert "warmup-secret" not in str(captured.value)


def test_runner_rejects_invalid_configuration() -> None:
    scenario = _scenario(lambda _: None)

    with pytest.raises(ValueError, match="warmup_iterations"):
        PerformanceBenchmarkRunner(scenarios=[scenario], warmup_iterations=-1)
    with pytest.raises(ValueError, match="measured_iterations"):
        PerformanceBenchmarkRunner(scenarios=[scenario], measured_iterations=0)
    with pytest.raises(ValueError, match="scenarios"):
        PerformanceBenchmarkRunner(scenarios=[])
    with pytest.raises(ValueError, match="unique"):
        PerformanceBenchmarkRunner(scenarios=[scenario, scenario])


def test_runner_rejects_budget_for_another_scenario() -> None:
    scenario = BenchmarkScenario(
        name=PerformanceScenarioName.POLICY_RAG_ANSWER,
        description="mismatch",
        operation=lambda _: None,
        budget=PerformanceBudget(
            scenario=PerformanceScenarioName.RUNTIME_STARTUP,
            max_p95_ms=100.0,
        ),
    )

    with pytest.raises(ValueError, match="budget"):
        PerformanceBenchmarkRunner(scenarios=[scenario])
