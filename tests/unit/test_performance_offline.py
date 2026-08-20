from __future__ import annotations

import math
import tomllib
from pathlib import Path

import pytest

from app.performance import (
    DeterministicHashEmbeddingProvider,
    OfflinePerformanceRuntime,
    PerformanceScenarioName,
    run_offline_performance_benchmark,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"


def test_hash_embedding_is_deterministic_normalized_and_fixed_dimension() -> None:
    provider = DeterministicHashEmbeddingProvider(dimension=16)

    first = provider.embed_query("差旅住宿")
    second = provider.embed_documents(["差旅住宿"])[0]

    assert first == second
    assert len(first) == 16
    assert math.sqrt(sum(value * value for value in first)) == pytest.approx(1.0)


@pytest.mark.parametrize("dimension", [7, 33])
def test_hash_embedding_rejects_unsupported_dimension(dimension: int) -> None:
    with pytest.raises(ValueError, match="dimension"):
        DeterministicHashEmbeddingProvider(dimension=dimension)


def test_offline_runtime_exposes_every_expected_scenario_once() -> None:
    runtime = OfflinePerformanceRuntime.build(_POLICY_DIRECTORY)
    scenarios = runtime.scenarios()

    assert {scenario.name for scenario in scenarios} == set(PerformanceScenarioName)
    assert len(scenarios) == len(PerformanceScenarioName)
    assert all(scenario.budget.scenario is scenario.name for scenario in scenarios)


async def test_offline_benchmark_runs_real_project_paths_without_live_io() -> None:
    report = await run_offline_performance_benchmark(
        policy_directory=_POLICY_DIRECTORY,
        warmup_iterations=0,
        measured_iterations=1,
    )

    assert report.quality_gate_passed is True
    assert report.network_calls is False
    assert report.live_llm_calls is False
    assert report.embedding_provider == "deterministic_hash_embedding_v1"
    assert len(report.scenario_results) == 5
    assert all(result.sample_count == 1 for result in report.scenario_results)
    assert all(result.error_count == 0 for result in report.scenario_results)
    assert len(report.bottleneck_candidates) == 5


def test_sampling_profilers_are_optional_not_runtime_dependencies() -> None:
    pyproject = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime_dependencies = pyproject["project"]["dependencies"]
    profiling_dependencies = pyproject["project"]["optional-dependencies"]["profiling"]

    assert all("py-spy" not in item and "scalene" not in item for item in runtime_dependencies)
    assert profiling_dependencies == [
        "py-spy>=0.4.2,<0.5.0",
        "scalene>=2.3,<3.0",
    ]
