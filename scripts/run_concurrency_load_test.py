from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.performance import (
    ConcurrencyLoadReport,
    run_offline_concurrency_load,
    write_concurrency_load_report,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT_DIRECTORY = _PROJECT_ROOT / "artifacts" / "performance"


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("必须是大于零的整数")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是大于零的数值")
    return parsed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="运行完全离线的 Agent 并发负载测试并生成吞吐与 p95 报告。"
    )
    parser.add_argument(
        "--requests",
        type=_positive_integer,
        default=24,
        help="每个请求分布场景发送的客户端请求数。",
    )
    parser.add_argument(
        "--concurrency",
        type=_positive_integer,
        default=12,
        help="每个场景允许同时执行的客户端任务数。",
    )
    parser.add_argument(
        "--provider-latency-ms",
        type=_positive_float,
        default=15.0,
        help="离线 Provider fixture 的固定异步 I/O 延迟。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIRECTORY,
        help="JSON 与 Markdown 报告输出目录。",
    )
    return parser.parse_args(argv)


async def run_load(
    *,
    request_count: int,
    concurrency: int,
    provider_latency_ms: float,
) -> ConcurrencyLoadReport:
    return await run_offline_concurrency_load(
        request_count=request_count,
        concurrency=concurrency,
        provider_latency_ms=provider_latency_ms,
    )


async def _run(args: argparse.Namespace) -> int:
    report = await run_load(
        request_count=args.requests,
        concurrency=args.concurrency,
        provider_latency_ms=args.provider_latency_ms,
    )
    paths = write_concurrency_load_report(report, args.output_dir)
    summary = {
        "quality_gate_passed": report.quality_gate_passed,
        "suite_name": report.suite_name,
        "schema_version": report.schema_version,
        "request_count": report.request_count,
        "configured_concurrency": report.configured_concurrency,
        "network_calls": report.network_calls,
        "live_llm_calls": report.live_llm_calls,
        "decision": report.decision,
        "scenarios": {
            result.scenario.value: {
                "p95_ms": round(result.p95_ms, 3),
                "throughput_rps": round(result.throughput_rps, 3),
                "upstream_calls": result.upstream_calls,
                "upstream_call_ratio": round(result.upstream_call_ratio, 6),
                "upstream_call_amplification": round(
                    result.upstream_call_amplification,
                    6,
                ),
                "provider_peak_in_flight": result.provider_peak_in_flight,
                "error_rate": result.error_rate,
                "meets_contract": result.meets_contract,
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
    except Exception as exc:  # noqa: BLE001 - CLI never prints exception text
        print(f"并发负载测试失败：{type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
