from __future__ import annotations

import platform
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeAlias

from app.performance.models import (
    BatchOptimizationReport,
    BatchOptimizationScenarioName,
    BatchOptimizationScenarioResult,
    PerformanceEnvironment,
)


@dataclass(frozen=True, slots=True)
class BatchExecutionObservation:
    """Stable evidence returned by one sequential or batched execution."""

    output_digest: str
    output_order: tuple[str, ...]
    provider_calls: int
    internal_batches: int

    def __post_init__(self) -> None:
        if not self.output_digest.strip():
            raise ValueError("output_digest must not be blank")
        if self.provider_calls < 1 or self.internal_batches < 1:
            raise ValueError("batch counters must be greater than zero")


BatchOperation: TypeAlias = Callable[[], BatchExecutionObservation]


@dataclass(frozen=True, slots=True)
class BatchOptimizationScenario:
    """One sequential-versus-batched model workload."""

    name: BatchOptimizationScenarioName
    description: str
    item_count: int
    batch_size: int
    expected_sequential_provider_calls: int
    expected_batched_provider_calls: int
    expected_sequential_internal_batches: int
    expected_batched_internal_batches: int
    run_sequential: BatchOperation
    run_batched: BatchOperation

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("scenario description must not be blank")
        if self.item_count < 1:
            raise ValueError("item_count must be greater than zero")
        if self.batch_size < 1:
            raise ValueError("batch_size must be greater than zero")
        expected_values = (
            self.expected_sequential_provider_calls,
            self.expected_batched_provider_calls,
            self.expected_sequential_internal_batches,
            self.expected_batched_internal_batches,
        )
        if any(value < 1 for value in expected_values):
            raise ValueError("expected batch counters must be greater than zero")


def _environment() -> PerformanceEnvironment:
    return PerformanceEnvironment(
        python_version=platform.python_version(),
        operating_system=platform.system() or sys.platform,
        machine=platform.machine() or "unknown",
    )


class BatchOptimizationRunner:
    """Measure call reduction and result equivalence for batch-first model APIs."""

    def __init__(
        self,
        *,
        scenarios: Sequence[BatchOptimizationScenario],
        simulated_call_overhead_ms: float,
        simulated_batch_latency_ms: float,
        monotonic_ns: Callable[[], int] | None = None,
        generated_at: Callable[[], datetime] | None = None,
    ) -> None:
        scenario_tuple = tuple(scenarios)
        if not scenario_tuple:
            raise ValueError("scenarios must not be empty")
        names = [scenario.name for scenario in scenario_tuple]
        if len(set(names)) != len(names):
            raise ValueError("scenario names must be unique")
        shapes = {(scenario.item_count, scenario.batch_size) for scenario in scenario_tuple}
        if len(shapes) != 1:
            raise ValueError("all scenarios must use the same batch shape")
        if simulated_call_overhead_ms <= 0:
            raise ValueError("simulated_call_overhead_ms must be greater than zero")
        if simulated_batch_latency_ms <= 0:
            raise ValueError("simulated_batch_latency_ms must be greater than zero")

        self._scenarios = scenario_tuple
        self._simulated_call_overhead_ms = simulated_call_overhead_ms
        self._simulated_batch_latency_ms = simulated_batch_latency_ms
        self._monotonic_ns = monotonic_ns or time.perf_counter_ns
        self._generated_at = generated_at or (lambda: datetime.now(UTC))

    def _measure(
        self,
        operation: BatchOperation,
    ) -> tuple[BatchExecutionObservation, float]:
        started_ns = self._monotonic_ns()
        observation = operation()
        finished_ns = self._monotonic_ns()
        duration_ms = max(1, finished_ns - started_ns) / 1_000_000
        return observation, duration_ms

    def _run_scenario(
        self,
        scenario: BatchOptimizationScenario,
    ) -> BatchOptimizationScenarioResult:
        sequential, sequential_duration_ms = self._measure(scenario.run_sequential)
        batched, batched_duration_ms = self._measure(scenario.run_batched)

        outputs_equivalent = sequential.output_digest == batched.output_digest
        order_preserved = (
            sequential.output_order == batched.output_order
            and len(batched.output_order) == scenario.item_count
        )
        sequential_throughput = scenario.item_count / (sequential_duration_ms / 1_000)
        batched_throughput = scenario.item_count / (batched_duration_ms / 1_000)
        speedup = sequential_duration_ms / batched_duration_ms
        call_reduction = 1 - (batched.provider_calls / sequential.provider_calls)
        meets_contract = (
            outputs_equivalent
            and order_preserved
            and sequential.provider_calls == scenario.expected_sequential_provider_calls
            and batched.provider_calls == scenario.expected_batched_provider_calls
            and sequential.internal_batches == scenario.expected_sequential_internal_batches
            and batched.internal_batches == scenario.expected_batched_internal_batches
        )

        return BatchOptimizationScenarioResult(
            scenario=scenario.name,
            description=scenario.description,
            item_count=scenario.item_count,
            configured_batch_size=scenario.batch_size,
            sequential_provider_calls=sequential.provider_calls,
            batched_provider_calls=batched.provider_calls,
            provider_call_reduction=call_reduction,
            sequential_internal_batches=sequential.internal_batches,
            batched_internal_batches=batched.internal_batches,
            sequential_duration_ms=sequential_duration_ms,
            batched_duration_ms=batched_duration_ms,
            sequential_throughput_items_per_second=sequential_throughput,
            batched_throughput_items_per_second=batched_throughput,
            throughput_speedup=speedup,
            outputs_equivalent=outputs_equivalent,
            order_preserved=order_preserved,
            batched_faster=batched_duration_ms < sequential_duration_ms,
            meets_contract=meets_contract,
        )

    def run(self) -> BatchOptimizationReport:
        started_ns = self._monotonic_ns()
        results = tuple(self._run_scenario(scenario) for scenario in self._scenarios)
        finished_ns = self._monotonic_ns()

        return BatchOptimizationReport(
            generated_at=self._generated_at(),
            item_count=self._scenarios[0].item_count,
            configured_batch_size=self._scenarios[0].batch_size,
            simulated_call_overhead_ms=self._simulated_call_overhead_ms,
            simulated_batch_latency_ms=self._simulated_batch_latency_ms,
            duration_ms=max(1, finished_ns - started_ns) / 1_000_000,
            network_calls=False,
            live_model_calls=False,
            environment=_environment(),
            quality_gate_passed=all(result.meets_contract for result in results),
            scenario_results=results,
        )
