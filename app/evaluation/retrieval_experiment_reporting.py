from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.evaluation.retrieval_experiment_models import CandidateWindowExperimentReport


@dataclass(frozen=True, slots=True)
class CandidateWindowReportPaths:
    json_path: Path
    markdown_path: Path


def _percentage(value: float) -> str:
    return f"{value * 100:.2f}%"


def render_candidate_window_markdown(report: CandidateWindowExperimentReport) -> str:
    gate = "通过" if report.quality_gate_passed else "未通过"
    lines = [
        "# 企业制度 Agent Candidate Window 消融报告",
        "",
        f"- 运行模式：`{report.evaluation_mode.value}`",
        f"- Embedding：`{report.embedding_provider}`",
        f"- Reranker：`{report.reranker_provider}`",
        f"- 请求设备：`{report.requested_device or 'auto'}`",
        f"- Embedding / Reranker batch size：{report.embedding_batch_size} / "
        f"{report.reranker_batch_size}",
        f"- 外部模型推理：`{str(report.external_model_calls).lower()}`",
        f"- 模型可能需要下载：`{str(report.model_download_may_be_required).lower()}`",
        f"- 环境：`{report.environment.operating_system}/{report.environment.machine}`，"
        f"Python `{report.environment.python_version}`",
        f"- Query / judgments：{report.total_cases} / {report.total_judgments}",
        f"- 最终 Top-K：{report.final_top_k}",
        f"- Candidate windows：{', '.join(str(value) for value in report.candidate_ks)}",
        f"- 当前默认窗口：{report.default_candidate_k}",
        f"- 预热 / 重复：{report.warmup_iterations} / {report.measured_repetitions}",
        f"- 默认窗口质量门禁：**{gate}**",
        "",
        "## 质量与延迟",
        "",
        "| 通道 | Candidate K | Recall@5 | MRR@5 | nDCG@5 | p50 | p95 | 错误 | 质量门禁 | Pareto |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for point in report.points:
        quality = "通过" if point.meets_quality_gate else "未通过"
        pareto = "是" if point.pareto_optimal else "否"
        default = "（当前默认）" if point.candidate_k == report.default_candidate_k else ""
        lines.append(
            f"| `{point.channel.value}` | {point.candidate_k}{default} | "
            f"{_percentage(point.recall_at_5)} | {_percentage(point.mrr_at_5)} | "
            f"{_percentage(point.ndcg_at_5)} | {point.p50_ms:.3f} ms | "
            f"{point.p95_ms:.3f} ms | {point.error_count} | {quality} | {pareto} |"
        )

    lines.extend(("", "## Pareto 前沿", ""))
    for channel, candidate_ks in report.pareto_frontier.items():
        values = ", ".join(str(value) for value in candidate_ks)
        lines.append(f"- `{channel.value}`：candidate K = {values}")

    lines.extend(
        (
            "",
            "## 决策边界",
            "",
            "- Sweep 只改变 candidate K，最终 Top-5、语料、judgments、授权身份和模型保持不变；",
            "- p95 使用 nearest-rank；不同机器和不同运行模式的绝对耗时不能直接横向比较；",
            "- Pareto 表示当前样本中不存在质量全面不差且 p95 更低的窗口，不等于自动推荐；",
            "- 报告不会修改生产配置；必须审核真实 BGE 固定硬件结果后再调整默认窗口；",
            "- `offline` 只验证实验方法，不代表真实 BGE 的质量、显存、吞吐或 SLA。",
            "",
        )
    )
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_candidate_window_report(
    report: CandidateWindowExperimentReport,
    output_directory: str | Path,
) -> CandidateWindowReportPaths:
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "retrieval-candidate-sweep-report.json"
    markdown_path = directory / "retrieval-candidate-sweep-report.md"
    _atomic_write(json_path, report.model_dump_json(indent=2) + "\n")
    _atomic_write(markdown_path, render_candidate_window_markdown(report))
    return CandidateWindowReportPaths(json_path=json_path, markdown_path=markdown_path)
