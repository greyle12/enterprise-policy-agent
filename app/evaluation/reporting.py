from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.evaluation.models import EvaluationReport

_METRIC_LABELS = {
    "intent_accuracy": "意图识别准确率",
    "tool_selection_accuracy": "工具选择准确率",
    "material_check_accuracy": "材料检查准确率",
    "approval_route_accuracy": "审批路线准确率",
    "citation_accuracy": "制度引用准确率",
}


@dataclass(frozen=True, slots=True)
class EvaluationReportPaths:
    """一次报告写入产生的两个本地文件。"""

    json_path: Path
    markdown_path: Path


def _percentage(value: float) -> str:
    return f"{value * 100:.2f}%"


def render_evaluation_markdown(report: EvaluationReport) -> str:
    """将结构化报告渲染为适合人工审阅的 Markdown。"""

    gate = "通过" if report.quality_gate_passed else "未通过"
    lines = [
        "# 企业制度 Agent 黄金集评测报告",
        "",
        f"- 运行模式：`{report.evaluation_mode.value}`",
        f"- 意图识别来源：`{report.intent_provider}`",
        (
            "- 意图识别是否调用真实 LLM："
            f"`{str(report.live_intent_llm_calls).lower()}`"
        ),
        f"- 数据集 SHA-256：`{report.dataset_sha256}`",
        f"- 生成时间：`{report.generated_at.isoformat()}`",
        f"- 总用例：{report.total_cases}",
        f"- 通过 / 失败：{report.passed_cases} / {report.failed_cases}",
        f"- 质量门禁：**{gate}**",
        "",
        "## 指标汇总",
        "",
        "| 指标 | 通过数 | 准确率 | 门槛 | 结果 |",
        "|---|---:|---:|---:|---|",
    ]

    for metric in report.metrics:
        label = _METRIC_LABELS[metric.metric.value]
        status = "通过" if metric.meets_threshold else "未通过"
        lines.append(
            f"| {label} | {metric.passed_cases}/{metric.total_cases} | "
            f"{_percentage(metric.accuracy)} | "
            f"{_percentage(metric.threshold)} | {status} |"
        )

    lines.extend(
        (
            "",
            "## 用例结果",
            "",
            "| 用例 | 类别 | 标题 | 耗时 | 结果 |",
            "|---|---|---|---:|---|",
        )
    )
    for case in report.case_results:
        status = "通过" if case.passed else "失败"
        safe_title = case.title.replace("|", "\\|")
        lines.append(
            f"| `{case.case_id}` | `{case.category.value}` | "
            f"{safe_title} | {case.duration_ms:.3f} ms | {status} |"
        )

    failed_assertions = [
        (case.case_id, dimension.metric.value, assertion)
        for case in report.case_results
        for dimension in case.dimensions
        for assertion in dimension.assertions
        if not assertion.passed
    ]
    lines.extend(("", "## 失败明细", ""))
    if not failed_assertions:
        lines.append("无失败断言。")
    else:
        lines.extend(
            (
                "| 用例 | 指标 | 断言 | 期望 | 实际 |",
                "|---|---|---|---|---|",
            )
        )
        for case_id, metric, assertion in failed_assertions:
            expected = str(assertion.expected).replace("|", "\\|")
            actual = str(assertion.actual).replace("|", "\\|")
            lines.append(
                f"| `{case_id}` | `{metric}` | `{assertion.name}` | "
                f"`{expected}` | `{actual}` |"
            )

    lines.extend(
        (
            "",
            (
                "> `offline` 模式使用确定性关键词意图基线，"
                "用于 CI 和评测链路回归；它不代表真实 LLM 的线上准确率。"
            ),
            "",
        )
    )
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_evaluation_report(
    report: EvaluationReport,
    output_directory: str | Path,
) -> EvaluationReportPaths:
    """原子写入 JSON 与 Markdown，避免留下半份报告。"""

    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "golden-evaluation-report.json"
    markdown_path = directory / "golden-evaluation-report.md"

    _atomic_write(
        json_path,
        report.model_dump_json(indent=2) + "\n",
    )
    _atomic_write(
        markdown_path,
        render_evaluation_markdown(report),
    )
    return EvaluationReportPaths(
        json_path=json_path,
        markdown_path=markdown_path,
    )
