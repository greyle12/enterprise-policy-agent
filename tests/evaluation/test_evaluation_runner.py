from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.agent.intent import IntentType
from app.evaluation.dataset import load_golden_dataset
from app.evaluation.models import (
    EvaluationMetric,
    EvaluationMode,
    GoldenRoutingCase,
)
from app.evaluation.reporting import write_evaluation_report
from app.evaluation.runner import GoldenEvaluationRunner
from app.evaluation.runtime import (
    OfflineIntentClassifier,
    build_evaluation_runtime,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_GOLDEN_DATASET = _PROJECT_ROOT / "tests" / "evaluation" / "golden_test_cases.jsonl"
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_FIXED_TIME = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _runner(dataset_sha256: str) -> GoldenEvaluationRunner:
    runtime = build_evaluation_runtime(
        policy_directory=_POLICY_DIRECTORY,
        intent_classifier=OfflineIntentClassifier(),
    )
    return GoldenEvaluationRunner(
        router=runtime.router,
        material_checker=runtime.material_checker,
        approval_checker=runtime.approval_checker,
        evaluation_mode=EvaluationMode.OFFLINE,
        intent_provider="deterministic_keyword_baseline_v1",
        dataset_sha256=dataset_sha256,
        clock=lambda: _FIXED_TIME,
    )


async def test_full_offline_suite_passes_all_quality_gates() -> None:
    dataset = load_golden_dataset(_GOLDEN_DATASET)

    report = await _runner(dataset.sha256).run(dataset.cases)

    assert report.total_cases == 30
    assert report.passed_cases == 30
    assert report.failed_cases == 0
    assert report.failed_case_ids == ()
    assert report.quality_gate_passed is True
    assert {
        item.metric: (item.passed_cases, item.total_cases, item.accuracy)
        for item in report.metrics
    } == {
        EvaluationMetric.INTENT_ACCURACY: (10, 10, 1.0),
        EvaluationMetric.TOOL_SELECTION_ACCURACY: (10, 10, 1.0),
        EvaluationMetric.MATERIAL_CHECK_ACCURACY: (10, 10, 1.0),
        EvaluationMetric.APPROVAL_ROUTE_ACCURACY: (10, 10, 1.0),
        EvaluationMetric.CITATION_ACCURACY: (20, 20, 1.0),
    }


async def test_quality_gate_fails_below_intent_threshold() -> None:
    dataset = load_golden_dataset(_GOLDEN_DATASET)
    cases = list(dataset.cases)
    for index in (0, 1):
        case = cases[index]
        assert isinstance(case, GoldenRoutingCase)
        cases[index] = case.model_copy(
            update={"expected_intent": IntentType.UNKNOWN}
        )

    report = await _runner(dataset.sha256).run(cases)
    intent_metric = next(
        item
        for item in report.metrics
        if item.metric is EvaluationMetric.INTENT_ACCURACY
    )

    assert intent_metric.accuracy == 0.8
    assert intent_metric.meets_threshold is False
    assert report.quality_gate_passed is False
    assert report.failed_case_ids[:2] == ("ROUTE-001", "ROUTE-002")


async def test_writes_json_and_markdown_reports(tmp_path: Path) -> None:
    dataset = load_golden_dataset(_GOLDEN_DATASET)
    report = await _runner(dataset.sha256).run(dataset.cases)

    paths = write_evaluation_report(report, tmp_path)

    assert paths.json_path.is_file()
    assert paths.markdown_path.is_file()
    assert '"quality_gate_passed": true' in paths.json_path.read_text(
        encoding="utf-8"
    )
    markdown = paths.markdown_path.read_text(encoding="utf-8")
    assert "意图识别准确率" in markdown
    assert "30 / 0" in markdown
    assert "离线" not in markdown or "offline" in markdown
