from __future__ import annotations

import argparse
import asyncio
import cProfile
import json
import sys
from pathlib import Path

from app.performance import (
    build_cprofile_report,
    write_cprofile_report,
    write_performance_report,
)
from scripts.run_performance_benchmark import run_benchmark

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT_DIRECTORY = _PROJECT_ROOT / "artifacts" / "performance"


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("必须是大于零的整数")
    return parsed


def _non_negative_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("必须是非负整数")
    return parsed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用 Python 内置 cProfile 定位 Day 22 离线基准中的项目函数热点。"
    )
    parser.add_argument("--warmups", type=_non_negative_integer, default=1)
    parser.add_argument("--iterations", type=_positive_integer, default=5)
    parser.add_argument("--top", type=_positive_integer, default=20)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIRECTORY,
    )
    return parser.parse_args(argv)


def _profile(args: argparse.Namespace) -> int:
    profiler = cProfile.Profile()
    report = profiler.runcall(
        lambda: asyncio.run(
            run_benchmark(
                warmups=args.warmups,
                iterations=args.iterations,
            )
        )
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_profile_path = args.output_dir / "agent-performance.cprofile"
    profiler.dump_stats(raw_profile_path)
    performance_paths = write_performance_report(report, args.output_dir)
    profile_report = build_cprofile_report(
        profiler,
        project_root=_PROJECT_ROOT,
        top_n=args.top,
    )
    profile_paths = write_cprofile_report(profile_report, args.output_dir)
    summary = {
        "quality_gate_passed": report.quality_gate_passed,
        "profiler": profile_report.profiler,
        "project_function_entries": profile_report.project_function_entries,
        "hotspot_count": len(profile_report.hotspots),
        "top_hotspots": [
            {
                "path": hotspot.path,
                "line": hotspot.line_number,
                "function": hotspot.function_name,
                "cumulative_time_ms": round(hotspot.cumulative_time_ms, 3),
            }
            for hotspot in profile_report.hotspots[:5]
        ],
        "raw_profile": str(raw_profile_path.resolve()),
        "benchmark_json": str(performance_paths.json_path.resolve()),
        "hotspots_json": str(profile_paths.json_path.resolve()),
        "hotspots_markdown": str(profile_paths.markdown_path.resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report.quality_gate_passed and profile_report.hotspots else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return _profile(args)
    except Exception as exc:  # noqa: BLE001 - CLI 不回显可能敏感的异常正文
        print(f"cProfile 分析失败：{type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
