from __future__ import annotations

import cProfile
import pstats
from datetime import UTC, datetime
from pathlib import Path

from app.performance.models import CProfileReport, ProfileHotspot


def _project_relative_path(filename: str, project_root: Path) -> Path | None:
    if filename.startswith("<"):
        return None
    path = Path(filename)
    if not path.is_absolute():
        path = project_root / path
    try:
        relative = path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return None
    if not relative.parts or relative.parts[0] != "app":
        return None
    return relative


def build_cprofile_report(
    profiler: cProfile.Profile,
    *,
    project_root: str | Path,
    top_n: int = 20,
    generated_at: datetime | None = None,
) -> CProfileReport:
    """提取项目代码热点；报告不包含机器上的绝对路径。"""

    if top_n < 1:
        raise ValueError("top_n must be greater than zero")
    root = Path(project_root).resolve()
    stats = pstats.Stats(profiler)
    entries: list[tuple[float, float, str, int, str, int, int]] = []
    for (filename, line_number, function_name), values in stats.stats.items():
        relative = _project_relative_path(filename, root)
        if relative is None:
            continue
        primitive_calls, total_calls, own_seconds, cumulative_seconds, _ = values
        entries.append(
            (
                cumulative_seconds,
                own_seconds,
                relative.as_posix(),
                line_number,
                function_name,
                primitive_calls,
                total_calls,
            )
        )
    entries.sort(key=lambda item: (-item[0], -item[1], item[2], item[3], item[4]))
    hotspots = tuple(
        ProfileHotspot(
            rank=rank,
            path=entry[2],
            line_number=entry[3],
            function_name=entry[4],
            primitive_calls=entry[5],
            total_calls=entry[6],
            own_time_ms=entry[1] * 1000,
            cumulative_time_ms=entry[0] * 1000,
        )
        for rank, entry in enumerate(entries[:top_n], start=1)
    )
    return CProfileReport(
        generated_at=generated_at or datetime.now(UTC),
        total_profiled_ms=stats.total_tt * 1000,
        total_function_entries=len(stats.stats),
        project_function_entries=len(entries),
        hotspots=hotspots,
    )
