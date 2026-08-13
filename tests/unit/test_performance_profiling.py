from __future__ import annotations

import cProfile
from pathlib import Path

import pytest

from app.performance import build_cprofile_report, nearest_rank_percentile

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_cprofile_report_contains_only_relative_project_paths() -> None:
    profiler = cProfile.Profile()
    profiler.runcall(nearest_rank_percentile, [5.0, 1.0, 3.0], 0.95)

    report = build_cprofile_report(
        profiler,
        project_root=_PROJECT_ROOT,
        top_n=5,
    )

    assert report.profiler == "cProfile"
    assert report.total_function_entries > 0
    assert report.project_function_entries > 0
    assert report.hotspots
    assert all(not Path(item.path).is_absolute() for item in report.hotspots)
    assert any(item.path == "app/performance/benchmark.py" for item in report.hotspots)


def test_cprofile_report_honors_top_n() -> None:
    profiler = cProfile.Profile()
    profiler.runcall(nearest_rank_percentile, [5.0, 1.0, 3.0], 0.50)

    report = build_cprofile_report(
        profiler,
        project_root=_PROJECT_ROOT,
        top_n=1,
    )

    assert len(report.hotspots) == 1
    assert report.hotspots[0].rank == 1


def test_cprofile_report_rejects_non_positive_top_n() -> None:
    with pytest.raises(ValueError, match="top_n"):
        build_cprofile_report(
            cProfile.Profile(),
            project_root=_PROJECT_ROOT,
            top_n=0,
        )
