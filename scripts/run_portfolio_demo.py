from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.portfolio import run_offline_portfolio_demo, write_portfolio_report

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_DEFAULT_OUTPUT_DIRECTORY = _PROJECT_ROOT / "artifacts" / "portfolio"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="运行 Day 30 完全离线作品集演示并生成 JSON/Markdown 证据。"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIRECTORY,
        help="作品集演示报告输出目录。",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    report = await run_offline_portfolio_demo(policy_directory=_POLICY_DIRECTORY)
    paths = write_portfolio_report(report, args.output_dir)
    summary = {
        "quality_gate_passed": report.quality_gate_passed,
        "release_label": report.release_label,
        "execution_mode": report.execution_mode,
        "policy_documents": report.policy_documents,
        "total_scenarios": report.total_scenarios,
        "passed_scenarios": report.passed_scenarios,
        "failed_scenarios": report.failed_scenarios,
        "network_calls": report.network_calls,
        "live_llm_calls": report.live_llm_calls,
        "scenarios": {result.scenario.value: result.passed for result in report.scenarios},
        "json_report": str(paths.json_path.resolve()),
        "markdown_report": str(paths.markdown_path.resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report.quality_gate_passed else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except Exception as exc:  # noqa: BLE001 - CLI prints only a stable exception type
        print(f"作品集演示失败：{type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
