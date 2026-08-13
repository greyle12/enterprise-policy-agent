from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.performance import (
    PerformanceReport,
    run_offline_performance_benchmark,
    write_performance_report,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
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
        description="运行完全离线的企业制度 Agent 性能基准并检查 p95 预算。"
    )
    parser.add_argument(
        "--warmups",
        type=_non_negative_integer,
        default=1,
        help="每个场景的预热次数；预热不进入统计。",
    )
    parser.add_argument(
        "--iterations",
        type=_positive_integer,
        default=5,
        help="每个场景进入报告的测量次数。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIRECTORY,
        help="JSON 与 Markdown 报告输出目录。",
    )
    return parser.parse_args(argv)


async def run_benchmark(
    *,
    warmups: int,
    iterations: int,
) -> PerformanceReport:
    return await run_offline_performance_benchmark(
        policy_directory=_POLICY_DIRECTORY,
        warmup_iterations=warmups,
        measured_iterations=iterations,
    )


async def _run(args: argparse.Namespace) -> int:
    report = await run_benchmark(
        warmups=args.warmups,
        iterations=args.iterations,
    )
    paths = write_performance_report(report, args.output_dir)
    summary = {
        "quality_gate_passed": report.quality_gate_passed,
        "suite_name": report.suite_name,
        "schema_version": report.schema_version,
        "warmup_iterations": report.warmup_iterations,
        "measured_iterations": report.measured_iterations,
        "network_calls": report.network_calls,
        "live_llm_calls": report.live_llm_calls,
        "bottleneck": report.bottleneck_candidates[0].scenario.value,
        "scenarios": {
            result.scenario.value: {
                "p50_ms": round(result.p50_ms, 3),
                "p95_ms": round(result.p95_ms, 3),
                "max_p95_ms": result.budget.max_p95_ms,
                "error_rate": result.error_rate,
                "meets_budget": result.meets_budget,
            }
            for result in report.scenario_results
        },
        "json_report": str(paths.json_path.resolve()),
        "markdown_report": str(paths.markdown_path.resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report.quality_gate_passed else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except Exception as exc:  # noqa: BLE001 - CLI 只返回稳定类型，不回显异常正文
        print(f"性能基准运行失败：{type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
