from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.evaluation.retrieval_models import RetrievalEvaluationReport


@dataclass(frozen=True, slots=True)
class RetrievalReportPaths:
    json_path: Path
    markdown_path: Path


def _percentage(value: float) -> str:
    return f"{value * 100:.2f}%"


def render_retrieval_markdown(report: RetrievalEvaluationReport) -> str:
    """Render metrics, ablation channels, and misses for human review."""

    gate = "通过" if report.quality_gate_passed else "未通过"
    gate_k = report.thresholds.gate_k
    lines = [
        "# 企业制度 Agent 检索评测报告",
        "",
        f"- 运行模式：`{report.evaluation_mode.value}`",
        f"- Embedding：`{report.embedding_provider}`",
        f"- Reranker：`{report.reranker_provider}`",
        f"- 外部模型推理：`{str(report.external_model_calls).lower()}`",
        f"- 数据集 SHA-256：`{report.dataset_sha256}`",
        f"- 语料 SHA-256：`{report.corpus_sha256}`",
        f"- 查询数：{report.total_cases}",
        f"- 候选池：Top {report.candidate_k}",
        f"- 质量门禁：**{gate}**",
        "",
        "## 消融指标",
        "",
        "| 检索通道 | "
        + " | ".join(f"Recall@{k}" for k in report.ks)
        + f" | MRR@{gate_k} | 平均耗时 | 错误 | 门禁 |",
        "|---|" + "---:|" * len(report.ks) + "---:|---:|---:|---|",
    ]
    for summary in report.summaries:
        if summary.meets_quality_gate is None:
            status = "仅对照"
        else:
            status = "通过" if summary.meets_quality_gate else "未通过"
        recalls = " | ".join(_percentage(summary.recall_at_k[k]) for k in report.ks)
        lines.append(
            f"| `{summary.channel.value}` | {recalls} | {_percentage(summary.mrr_at_k)} | "
            f"{summary.average_duration_ms:.3f} ms | {summary.error_count} | {status} |"
        )

    lines.extend(
        (
            "",
            "## 查询明细",
            "",
            f"| 用例 | 通道 | Recall@{gate_k} | 首个相关排名 | Top {max(report.ks)} | 错误 |",
            "|---|---|---:|---:|---|---|",
        )
    )
    for case in report.case_results:
        for result in case.channels:
            rank = str(result.first_relevant_rank) if result.first_relevant_rank else "—"
            ids = "<br>".join(f"`{chunk_id}`" for chunk_id in result.retrieved_chunk_ids)
            error = (result.error or "").replace("|", "\\|")
            lines.append(
                f"| `{case.case_id}` {case.title} | `{result.channel.value}` | "
                f"{_percentage(result.recall_at_k[gate_k])} | {rank} | {ids} | {error} |"
            )

    lines.extend(
        (
            "",
            "> `offline` 使用确定性哈希词法向量和词项重排，仅验证数据集、指标、",
            "> 授权边界与 CI 回归链路；它不代表真实 BGE 语义质量，必须使用 `--mode bge` 重新测量。",
            "",
        )
    )
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_retrieval_report(
    report: RetrievalEvaluationReport,
    output_directory: str | Path,
) -> RetrievalReportPaths:
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "retrieval-evaluation-report.json"
    markdown_path = directory / "retrieval-evaluation-report.md"
    _atomic_write(json_path, report.model_dump_json(indent=2) + "\n")
    _atomic_write(markdown_path, render_retrieval_markdown(report))
    return RetrievalReportPaths(json_path=json_path, markdown_path=markdown_path)
