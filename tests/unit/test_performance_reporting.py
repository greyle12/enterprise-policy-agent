from __future__ import annotations

import cProfile
import json
from pathlib import Path

from app.performance import (
    build_cprofile_report,
    nearest_rank_percentile,
    render_cprofile_markdown,
    render_performance_markdown,
    run_offline_performance_benchmark,
    write_cprofile_report,
    write_performance_report,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"


async def test_performance_report_writes_json_and_markdown(tmp_path: Path) -> None:
    report = await run_offline_performance_benchmark(
        policy_directory=_POLICY_DIRECTORY,
        warmup_iterations=0,
        measured_iterations=1,
    )

    paths = write_performance_report(report, tmp_path)
    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
    markdown = paths.markdown_path.read_text(encoding="utf-8")

    assert payload["quality_gate_passed"] is True
    assert len(payload["scenario_results"]) == 5
    assert "完全离线" in markdown
    assert "不代表真实 BGE、LLM 或网络延迟" in markdown
    assert render_performance_markdown(report) == markdown


def test_cprofile_report_writes_json_and_markdown(tmp_path: Path) -> None:
    profiler = cProfile.Profile()
    profiler.runcall(nearest_rank_percentile, [1.0, 2.0, 3.0], 0.95)
    report = build_cprofile_report(
        profiler,
        project_root=_PROJECT_ROOT,
        top_n=5,
    )

    paths = write_cprofile_report(report, tmp_path)
    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
    markdown = paths.markdown_path.read_text(encoding="utf-8")

    assert payload["profiler"] == "cProfile"
    assert payload["hotspots"]
    assert "累计耗时" in markdown
    assert "确定性插桩分析" in markdown
    assert render_cprofile_markdown(report) == markdown
