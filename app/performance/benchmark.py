from __future__ import annotations

import inspect
import platform
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from statistics import fmean
from typing import TypeAlias

from app.performance.models import (
    BottleneckCandidate,
    PerformanceBudget,
    PerformanceEnvironment,
    PerformanceReport,
    PerformanceSample,
    PerformanceScenarioName,
    PerformanceScenarioResult,
)

BenchmarkResult: TypeAlias = object | Awaitable[object]
BenchmarkOperation: TypeAlias = Callable[[int], BenchmarkResult]


@dataclass(frozen=True, slots=True)
class BenchmarkScenario:
    """一个可重复执行的离线基准场景。"""

    name: PerformanceScenarioName
    description: str
    operation: BenchmarkOperation
    budget: PerformanceBudget


class BenchmarkWarmupError(RuntimeError):
    """预热失败时安全停止，不把异常正文带入报告。"""

    def __init__(self, scenario: PerformanceScenarioName, error_type: str) -> None:
        super().__init__(f"warmup failed for {scenario.value}: {error_type}")
        self.scenario = scenario
        self.error_type = error_type


def nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    """使用 nearest-rank 计算小样本也可解释的分位数。"""

    if not values:
        raise ValueError("values must not be empty")
    if not 0.0 < percentile <= 1.0:
        raise ValueError("percentile must be greater than zero and at most one")
    ordered = sorted(float(value) for value in values)
    rank = max(1, ceil(percentile * len(ordered)))
    return ordered[rank - 1]


async def _invoke(operation: BenchmarkOperation, iteration: int) -> None:
    value = operation(iteration)
    if inspect.isawaitable(value):
        await value


def _environment() -> PerformanceEnvironment:
    return PerformanceEnvironment(
        python_version=platform.python_version(),
        operating_system=platform.system() or sys.platform,
        machine=platform.machine() or "unknown",
    )


class PerformanceBenchmarkRunner:
    """串行预热并测量场景，避免并发噪声掩盖单请求基线。"""

    def __init__(
        self,
        *,
        scenarios: Sequence[BenchmarkScenario],
        warmup_iterations: int = 1,
        measured_iterations: int = 5,
        monotonic_ns: Callable[[], int] | None = None,
        generated_at: Callable[[], datetime] | None = None,
    ) -> None:
        if warmup_iterations < 0:
            raise ValueError("warmup_iterations must not be negative")
        if measured_iterations < 1:
            raise ValueError("measured_iterations must be greater than zero")
        scenario_tuple = tuple(scenarios)
        if not scenario_tuple:
            raise ValueError("scenarios must not be empty")
        scenario_names = [scenario.name for scenario in scenario_tuple]
        if len(set(scenario_names)) != len(scenario_names):
            raise ValueError("scenario names must be unique")
        if any(scenario.budget.scenario is not scenario.name for scenario in scenario_tuple):
            raise ValueError("each budget must match its scenario")

        self._scenarios = scenario_tuple
        self._warmup_iterations = warmup_iterations
        self._measured_iterations = measured_iterations
        self._monotonic_ns = monotonic_ns or time.perf_counter_ns
        self._generated_at = generated_at or (lambda: datetime.now(UTC))

    async def _warm_up(self, scenario: BenchmarkScenario) -> None:
        for warmup in range(self._warmup_iterations):
            try:
                await _invoke(scenario.operation, -(warmup + 1))
            except Exception as exc:  # noqa: BLE001 - 转换为不含正文的稳定错误
                raise BenchmarkWarmupError(
                    scenario.name,
                    type(exc).__name__,
                ) from exc

    async def _measure(self, scenario: BenchmarkScenario) -> tuple[PerformanceSample, ...]:
        samples: list[PerformanceSample] = []
        for iteration in range(self._measured_iterations):
            started_ns = self._monotonic_ns()
            succeeded = True
            error_type: str | None = None
            try:
                await _invoke(scenario.operation, iteration)
            except Exception as exc:  # noqa: BLE001 - 报告仅记录类型并继续收集证据
                succeeded = False
                error_type = type(exc).__name__
            finished_ns = self._monotonic_ns()
            elapsed_ns = max(0, finished_ns - started_ns)
            samples.append(
                PerformanceSample(
                    iteration=iteration + 1,
                    duration_ms=elapsed_ns / 1_000_000,
                    succeeded=succeeded,
                    error_type=error_type,
                )
            )
        return tuple(samples)

    @staticmethod
    def _summarize(
        scenario: BenchmarkScenario,
        samples: tuple[PerformanceSample, ...],
    ) -> PerformanceScenarioResult:
        durations = [sample.duration_ms for sample in samples]
        error_count = sum(not sample.succeeded for sample in samples)
        error_rate = error_count / len(samples)
        p95_ms = nearest_rank_percentile(durations, 0.95)
        meets_budget = (
            p95_ms <= scenario.budget.max_p95_ms and error_rate <= scenario.budget.max_error_rate
        )
        return PerformanceScenarioResult(
            scenario=scenario.name,
            description=scenario.description,
            sample_count=len(samples),
            error_count=error_count,
            error_rate=error_rate,
            minimum_ms=min(durations),
            average_ms=fmean(durations),
            p50_ms=nearest_rank_percentile(durations, 0.50),
            p95_ms=p95_ms,
            maximum_ms=max(durations),
            budget=scenario.budget,
            budget_utilization=p95_ms / scenario.budget.max_p95_ms,
            meets_budget=meets_budget,
            samples=samples,
        )

    @staticmethod
    def _rank_bottlenecks(
        results: Sequence[PerformanceScenarioResult],
    ) -> tuple[BottleneckCandidate, ...]:
        ordered = sorted(
            results,
            key=lambda result: (-result.p95_ms, result.scenario.value),
        )
        slowest = ordered[0].p95_ms
        return tuple(
            BottleneckCandidate(
                rank=rank,
                scenario=result.scenario,
                p95_ms=result.p95_ms,
                share_of_slowest=(result.p95_ms / slowest if slowest > 0 else 0.0),
                budget_utilization=result.budget_utilization,
            )
            for rank, result in enumerate(ordered, start=1)
        )

    async def run(self) -> PerformanceReport:
        results: list[PerformanceScenarioResult] = []
        for scenario in self._scenarios:
            await self._warm_up(scenario)
            samples = await self._measure(scenario)
            results.append(self._summarize(scenario, samples))

        duration_ms = sum(sample.duration_ms for result in results for sample in result.samples)
        return PerformanceReport(
            generated_at=self._generated_at(),
            warmup_iterations=self._warmup_iterations,
            measured_iterations=self._measured_iterations,
            duration_ms=duration_ms,
            network_calls=False,
            live_llm_calls=False,
            embedding_provider="deterministic_hash_embedding_v1",
            environment=_environment(),
            quality_gate_passed=all(result.meets_budget for result in results),
            bottleneck_candidates=self._rank_bottlenecks(results),
            scenario_results=tuple(results),
        )
