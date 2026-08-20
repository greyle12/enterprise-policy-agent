from __future__ import annotations

import asyncio
import cProfile
import json
from pathlib import Path

from app.performance import PerformanceScenarioName, build_cprofile_report
from scripts.run_performance_benchmark import run_benchmark

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_EXPECTED_SCENARIOS = {item.value for item in PerformanceScenarioName}


def run_verification() -> dict[str, object]:
    """完全离线验收预热、分位数、预算、边界和 cProfile 热点。"""

    benchmark = asyncio.run(
        run_benchmark(
            warmups=1,
            iterations=3,
        )
    )
    profiler = cProfile.Profile()
    profiler.runcall(
        lambda: asyncio.run(
            run_benchmark(
                warmups=0,
                iterations=1,
            )
        )
    )
    profile = build_cprofile_report(
        profiler,
        project_root=_PROJECT_ROOT,
        top_n=15,
    )
    observed_scenarios = {result.scenario.value for result in benchmark.scenario_results}
    checks = {
        "all_scenarios_covered": observed_scenarios == _EXPECTED_SCENARIOS,
        "warmups_excluded": all(
            result.sample_count == benchmark.measured_iterations
            for result in benchmark.scenario_results
        ),
        "no_network_calls": benchmark.network_calls is False,
        "no_live_llm_calls": benchmark.live_llm_calls is False,
        "all_samples_succeeded": all(
            result.error_count == 0 for result in benchmark.scenario_results
        ),
        "all_budgets_passed": benchmark.quality_gate_passed,
        "bottleneck_ranking_complete": (
            len(benchmark.bottleneck_candidates) == len(_EXPECTED_SCENARIOS)
        ),
        "cprofile_found_project_hotspots": bool(profile.hotspots),
        "profile_paths_are_relative": all(
            not Path(hotspot.path).is_absolute() for hotspot in profile.hotspots
        ),
    }
    return {
        "passed": all(checks.values()),
        "suite_name": benchmark.suite_name,
        "schema_version": benchmark.schema_version,
        "warmup_iterations": benchmark.warmup_iterations,
        "measured_iterations": benchmark.measured_iterations,
        "network_calls": benchmark.network_calls,
        "live_llm_calls": benchmark.live_llm_calls,
        "quality_gate_passed": benchmark.quality_gate_passed,
        "bottleneck": benchmark.bottleneck_candidates[0].scenario.value,
        "scenario_p95_ms": {
            result.scenario.value: round(result.p95_ms, 3) for result in benchmark.scenario_results
        },
        "profile": {
            "profiler": profile.profiler,
            "project_function_entries": profile.project_function_entries,
            "hotspot_count": len(profile.hotspots),
            "top_hotspot": (
                f"{profile.hotspots[0].path}:"
                f"{profile.hotspots[0].line_number}:"
                f"{profile.hotspots[0].function_name}"
                if profile.hotspots
                else None
            ),
        },
        "checks": checks,
    }


def main() -> int:
    report = run_verification()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
