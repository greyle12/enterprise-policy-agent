from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.performance import (
    BatchOptimizationReport,
    run_offline_batch_optimization,
    write_batch_optimization_report,
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
        description="比较 Embedding/Reranker 逐条调用与批量调用并生成离线报告。"
    )
    parser.add_argument(
        "--items",
        type=_positive_integer,
        default=32,
        help="每个场景处理的文本或候选数量。",
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_integer,
        default=8,
        help="离线模型替身使用的内部 batch size。",
    )
    parser.add_argument(
        "--call-overhead-ms",
        type=_positive_float,
        default=1.5,
        help="每次 Provider 调用模拟的固定开销。",
    )
    parser.add_argument(
        "--batch-latency-ms",
        type=_positive_float,
        default=0.25,
        help="每个内部推理批次模拟的处理开销。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIRECTORY,
        help="JSON 与 Markdown 报告输出目录。",
    )
    return parser.parse_args(argv)


def run_comparison(
    *,
    item_count: int,
    batch_size: int,
    call_overhead_ms: float,
    batch_latency_ms: float,
) -> BatchOptimizationReport:
    return run_offline_batch_optimization(
        item_count=item_count,
        batch_size=batch_size,
        call_overhead_ms=call_overhead_ms,
        batch_latency_ms=batch_latency_ms,
    )


def _run(args: argparse.Namespace) -> int:
    report = run_comparison(
        item_count=args.items,
        batch_size=args.batch_size,
        call_overhead_ms=args.call_overhead_ms,
        batch_latency_ms=args.batch_latency_ms,
    )
    paths = write_batch_optimization_report(report, args.output_dir)
    summary = {
        "quality_gate_passed": report.quality_gate_passed,
        "suite_name": report.suite_name,
        "schema_version": report.schema_version,
        "item_count": report.item_count,
        "configured_batch_size": report.configured_batch_size,
        "network_calls": report.network_calls,
        "live_model_calls": report.live_model_calls,
        "decision": report.decision,
        "scenarios": {
            result.scenario.value: {
                "sequential_provider_calls": result.sequential_provider_calls,
                "batched_provider_calls": result.batched_provider_calls,
                "sequential_internal_batches": result.sequential_internal_batches,
                "batched_internal_batches": result.batched_internal_batches,
                "provider_call_reduction": round(result.provider_call_reduction, 6),
                "throughput_speedup": round(result.throughput_speedup, 6),
                "outputs_equivalent": result.outputs_equivalent,
                "order_preserved": result.order_preserved,
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
        return _run(args)
    except Exception as exc:  # noqa: BLE001 - CLI prints only a stable exception type
        print(f"批处理优化测试失败：{type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
