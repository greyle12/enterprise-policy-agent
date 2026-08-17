from __future__ import annotations

import asyncio
import platform
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import fmean
from typing import TypeAlias

from app.performance.benchmark import nearest_rank_percentile
from app.performance.models import (
    ConcurrencyLoadReport,
    ConcurrencyLoadSample,
    ConcurrencyLoadScenarioName,
    ConcurrencyLoadScenarioResult,
    PerformanceEnvironment,
)

ConcurrencyLoadOperation: TypeAlias = Callable[[int], Awaitable[object]]
ConcurrencyMetricsOperation: TypeAlias = Callable[[], Awaitable["ConcurrencyObservedMetrics"]]
ConcurrencyCloseOperation: TypeAlias = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ConcurrencyObservedMetrics:
    """Scenario-owned counters captured after all client requests finish."""

    upstream_calls: int
    provider_peak_in_flight: int
    cache_hits: int = 0
    coalesced_requests: int = 0

    def __post_init__(self) -> None:
        values = (
            self.upstream_calls,
            self.provider_peak_in_flight,
            self.cache_hits,
            self.coalesced_requests,
        )
        if any(value < 0 for value in values):
            raise ValueError("observed concurrency metrics must not be negative")


@dataclass(frozen=True, slots=True)
class ConcurrencyLoadScenario:
    """One controlled request distribution and its isolated resources."""

    name: ConcurrencyLoadScenarioName
    description: str
    request_count: int
    concurrency: int
    unique_request_keys: int
    expected_upstream_calls: int
    operation: ConcurrencyLoadOperation
    observe: ConcurrencyMetricsOperation
    close: ConcurrencyCloseOperation

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("scenario description must not be blank")
        if self.request_count < 1:
            raise ValueError("request_count must be greater than zero")
        if self.concurrency < 1:
            raise ValueError("concurrency must be greater than zero")
        if self.unique_request_keys < 1:
            raise ValueError("unique_request_keys must be greater than zero")
        if self.unique_request_keys > self.request_count:
            raise ValueError("unique_request_keys must not exceed request_count")
        if self.expected_upstream_calls < 1:
            raise ValueError("expected_upstream_calls must be greater than zero")


def _environment() -> PerformanceEnvironment:
    return PerformanceEnvironment(
        python_version=platform.python_version(),
        operating_system=platform.system() or sys.platform,
        machine=platform.machine() or "unknown",
    )


class ConcurrencyLoadRunner:
    """Launch bounded client tasks and retain queueing in end-to-end latency."""

    def __init__(
        self,
        *,
        scenarios: Sequence[ConcurrencyLoadScenario],
        simulated_provider_latency_ms: float,
        monotonic_ns: Callable[[], int] | None = None,
        generated_at: Callable[[], datetime] | None = None,
    ) -> None:
        scenario_tuple = tuple(scenarios)
        if not scenario_tuple:
            raise ValueError("scenarios must not be empty")
        names = [scenario.name for scenario in scenario_tuple]
        if len(set(names)) != len(names):
            raise ValueError("scenario names must be unique")
        if simulated_provider_latency_ms <= 0:
            raise ValueError("simulated_provider_latency_ms must be greater than zero")
        request_counts = {scenario.request_count for scenario in scenario_tuple}
        concurrency_values = {scenario.concurrency for scenario in scenario_tuple}
        if len(request_counts) != 1 or len(concurrency_values) != 1:
            raise ValueError("all scenarios must use the same load shape")

        self._scenarios = scenario_tuple
        self._simulated_provider_latency_ms = simulated_provider_latency_ms
        self._monotonic_ns = monotonic_ns or time.perf_counter_ns
        self._generated_at = generated_at or (lambda: datetime.now(UTC))

    async def _run_scenario(
        self,
        scenario: ConcurrencyLoadScenario,
    ) -> ConcurrencyLoadScenarioResult:
        semaphore = asyncio.Semaphore(scenario.concurrency)
        start_gate = asyncio.Event()
        active = 0
        peak_active = 0

        async def execute(request_index: int) -> ConcurrencyLoadSample:
            nonlocal active, peak_active
            await start_gate.wait()
            started_ns = self._monotonic_ns()
            succeeded = True
            error_type: str | None = None
            async with semaphore:
                active += 1
                peak_active = max(peak_active, active)
                try:
                    await scenario.operation(request_index)
                except Exception as exc:  # noqa: BLE001 - report only stable type
                    succeeded = False
                    error_type = type(exc).__name__
                finally:
                    active -= 1
            finished_ns = self._monotonic_ns()
            return ConcurrencyLoadSample(
                request_id=request_index + 1,
                duration_ms=max(0, finished_ns - started_ns) / 1_000_000,
                succeeded=succeeded,
                error_type=error_type,
            )

        tasks = tuple(
            asyncio.create_task(
                execute(request_index),
                name=f"concurrency-load-{scenario.name.value}",
            )
            for request_index in range(scenario.request_count)
        )
        scenario_started_ns = self._monotonic_ns()
        start_gate.set()
        try:
            samples = tuple(await asyncio.gather(*tasks))
            observed = await scenario.observe()
        finally:
            await scenario.close()
        scenario_finished_ns = self._monotonic_ns()

        duration_ms = max(1, scenario_finished_ns - scenario_started_ns) / 1_000_000
        durations = [sample.duration_ms for sample in samples]
        error_count = sum(not sample.succeeded for sample in samples)
        error_rate = error_count / len(samples)
        upstream_call_ratio = observed.upstream_calls / scenario.request_count
        amplification = observed.upstream_calls / scenario.unique_request_keys
        meets_contract = (
            error_count == 0
            and observed.upstream_calls == scenario.expected_upstream_calls
            and peak_active <= scenario.concurrency
            and observed.provider_peak_in_flight <= scenario.concurrency
        )

        return ConcurrencyLoadScenarioResult(
            scenario=scenario.name,
            description=scenario.description,
            request_count=scenario.request_count,
            configured_concurrency=scenario.concurrency,
            unique_request_keys=scenario.unique_request_keys,
            duration_ms=duration_ms,
            throughput_rps=scenario.request_count / (duration_ms / 1_000),
            error_count=error_count,
            error_rate=error_rate,
            minimum_ms=min(durations),
            average_ms=fmean(durations),
            p50_ms=nearest_rank_percentile(durations, 0.50),
            p95_ms=nearest_rank_percentile(durations, 0.95),
            maximum_ms=max(durations),
            client_peak_in_flight=peak_active,
            provider_peak_in_flight=observed.provider_peak_in_flight,
            expected_upstream_calls=scenario.expected_upstream_calls,
            upstream_calls=observed.upstream_calls,
            upstream_call_ratio=upstream_call_ratio,
            upstream_call_amplification=amplification,
            cache_hits=observed.cache_hits,
            coalesced_requests=observed.coalesced_requests,
            meets_contract=meets_contract,
            samples=samples,
        )

    async def run(self) -> ConcurrencyLoadReport:
        results: list[ConcurrencyLoadScenarioResult] = []
        for scenario in self._scenarios:
            results.append(await self._run_scenario(scenario))

        return ConcurrencyLoadReport(
            generated_at=self._generated_at(),
            request_count=self._scenarios[0].request_count,
            configured_concurrency=self._scenarios[0].concurrency,
            simulated_provider_latency_ms=self._simulated_provider_latency_ms,
            duration_ms=sum(result.duration_ms for result in results),
            network_calls=False,
            live_llm_calls=False,
            environment=_environment(),
            quality_gate_passed=all(result.meets_contract for result in results),
            scenario_results=tuple(results),
        )
