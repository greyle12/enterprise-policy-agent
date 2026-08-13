from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.performance.models import CProfileReport, PerformanceReport


@dataclass(frozen=True, slots=True)
class PerformanceReportPaths:
    json_path: Path
    markdown_path: Path


@dataclass(frozen=True, slots=True)
class CProfileReportPaths:
    json_path: Path
    markdown_path: Path


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def render_performance_markdown(report: PerformanceReport) -> str:
    gate = "通过" if report.quality_gate_passed else "未通过"
    lines = [
        "# 企业制度 Agent 离线性能基准报告",
        "",
        f"- 基准版本：`{report.schema_version}`",
        f"- 生成时间：`{report.generated_at.isoformat()}`",
        f"- 预热 / 测量次数：{report.warmup_iterations} / {report.measured_iterations}",
        f"- Python：`{report.environment.python_version}`",
        (f"- 平台：`{report.environment.operating_system}/{report.environment.machine}`"),
        f"- 性能预算：`{report.budget_source}`",
        f"- 质量门禁：**{gate}**",
        "",
        "> 本报告完全离线，使用确定性 Hash Embedding、固定 LLM 返回和固定 Web 结果。",
        "> 它测量本项目的解析、检索、规则和编排开销，不代表真实 BGE、LLM 或网络延迟。",
        "",
        "## 场景结果",
        "",
        "| 场景 | 样本 | 平均 | p50 | p95 | 最大 | p95 预算 | 错误率 | 结果 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in report.scenario_results:
        status = "通过" if result.meets_budget else "未通过"
        lines.append(
            f"| `{result.scenario.value}` | {result.sample_count} | "
            f"{result.average_ms:.3f} ms | {result.p50_ms:.3f} ms | "
            f"{result.p95_ms:.3f} ms | {result.maximum_ms:.3f} ms | "
            f"{result.budget.max_p95_ms:.1f} ms | {result.error_rate:.2%} | {status} |"
        )

    lines.extend(("", "## 候选瓶颈", ""))
    for candidate in report.bottleneck_candidates:
        lines.append(
            f"{candidate.rank}. `{candidate.scenario.value}`："
            f"p95 {candidate.p95_ms:.3f} ms，"
            f"预算占用 {candidate.budget_utilization:.2%}。"
        )
    lines.extend(
        (
            "",
            "## 解释边界",
            "",
            "- warm-up 不计入统计，减少首次导入和缓存建立的干扰；",
            "- 所有场景串行执行，基线针对单请求开销，不代表吞吐量；",
            "- 小样本 p95 使用 nearest-rank，便于本地和 CI 重复验收；",
            "- 不同机器的绝对耗时不能直接横向比较，应在同环境观察趋势；",
            "- 候选瓶颈只是进一步用 cProfile、py-spy 或 Scalene 验证的入口。",
            "",
        )
    )
    return "\n".join(lines)


def render_cprofile_markdown(report: CProfileReport) -> str:
    lines = [
        "# 企业制度 Agent cProfile 热点报告",
        "",
        f"- 生成时间：`{report.generated_at.isoformat()}`",
        f"- 总函数条目：{report.total_function_entries}",
        f"- 项目函数条目：{report.project_function_entries}",
        f"- 统计 CPU 时间：{report.total_profiled_ms:.3f} ms",
        f"- 排序方式：`{report.sort_key}`",
        "",
        "| 排名 | 项目函数 | 调用数 | 自身耗时 | 累计耗时 |",
        "|---:|---|---:|---:|---:|",
    ]
    for hotspot in report.hotspots:
        location = f"{hotspot.path}:{hotspot.line_number}:{hotspot.function_name}"
        lines.append(
            f"| {hotspot.rank} | `{location}` | {hotspot.total_calls} | "
            f"{hotspot.own_time_ms:.3f} ms | {hotspot.cumulative_time_ms:.3f} ms |"
        )
    lines.extend(
        (
            "",
            "> cProfile 是确定性插桩分析，会增加运行开销；",
            "> 请把它用于定位调用热点，不要把其耗时当作无探针性能基线。",
            "",
        )
    )
    return "\n".join(lines)


def write_performance_report(
    report: PerformanceReport,
    output_directory: str | Path,
) -> PerformanceReportPaths:
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "agent-performance-report.json"
    markdown_path = directory / "agent-performance-report.md"
    _atomic_write(json_path, report.model_dump_json(indent=2) + "\n")
    _atomic_write(markdown_path, render_performance_markdown(report))
    return PerformanceReportPaths(json_path=json_path, markdown_path=markdown_path)


def write_cprofile_report(
    report: CProfileReport,
    output_directory: str | Path,
) -> CProfileReportPaths:
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "agent-cprofile-hotspots.json"
    markdown_path = directory / "agent-cprofile-hotspots.md"
    _atomic_write(json_path, report.model_dump_json(indent=2) + "\n")
    _atomic_write(markdown_path, render_cprofile_markdown(report))
    return CProfileReportPaths(json_path=json_path, markdown_path=markdown_path)
