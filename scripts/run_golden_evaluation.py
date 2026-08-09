from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.agent.intent_classifier import IntentClassifier
from app.core.config import get_settings
from app.evaluation.dataset import GoldenDatasetError, load_golden_dataset
from app.evaluation.models import EvaluationMode
from app.evaluation.reporting import write_evaluation_report
from app.evaluation.runner import GoldenEvaluationRunner
from app.evaluation.runtime import (
    OfflineIntentClassifier,
    build_evaluation_runtime,
)
from app.llm.openai_compatible_client import OpenAICompatibleLLMClient

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATASET = _PROJECT_ROOT / "tests" / "evaluation" / "golden_test_cases.jsonl"
_DEFAULT_OUTPUT_DIRECTORY = _PROJECT_ROOT / "artifacts" / "evaluation"
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "运行企业制度 Agent 的 30 条黄金用例，"
            "并输出 JSON 与 Markdown 评测报告。"
        )
    )
    parser.add_argument(
        "--mode",
        choices=[item.value for item in EvaluationMode],
        default=EvaluationMode.OFFLINE.value,
        help=(
            "offline 使用确定性意图基线且不联网；"
            "live 使用 .env 中配置的真实 LLM 进行意图识别。"
        ),
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=_DEFAULT_DATASET,
        help="黄金 JSONL 数据集路径。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIRECTORY,
        help="JSON 与 Markdown 报告输出目录。",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    dataset = load_golden_dataset(args.dataset)
    mode = EvaluationMode(args.mode)
    llm_client: OpenAICompatibleLLMClient | None = None

    if mode is EvaluationMode.LIVE:
        settings = get_settings()
        llm_client = OpenAICompatibleLLMClient.from_settings(settings)
        classifier = IntentClassifier(llm_client=llm_client)
        intent_provider = settings.llm_model
    else:
        classifier = OfflineIntentClassifier()
        intent_provider = "deterministic_keyword_baseline_v1"

    try:
        runtime = build_evaluation_runtime(
            policy_directory=_POLICY_DIRECTORY,
            intent_classifier=classifier,
        )
        runner = GoldenEvaluationRunner(
            router=runtime.router,
            material_checker=runtime.material_checker,
            approval_checker=runtime.approval_checker,
            evaluation_mode=mode,
            intent_provider=intent_provider,
            dataset_sha256=dataset.sha256,
        )
        report = await runner.run(dataset.cases)
        paths = write_evaluation_report(report, args.output_dir)
    finally:
        if llm_client is not None:
            await llm_client.close()

    summary = {
        "quality_gate_passed": report.quality_gate_passed,
        "evaluation_mode": report.evaluation_mode.value,
        "total_cases": report.total_cases,
        "passed_cases": report.passed_cases,
        "failed_cases": report.failed_cases,
        "metrics": {
            item.metric.value: {
                "passed": item.passed_cases,
                "total": item.total_cases,
                "accuracy": item.accuracy,
                "threshold": item.threshold,
                "meets_threshold": item.meets_threshold,
            }
            for item in report.metrics
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
    except GoldenDatasetError as exc:
        print(f"黄金集加载失败：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI 需要稳定退出码和简洁错误
        print(
            f"评测运行失败：{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
