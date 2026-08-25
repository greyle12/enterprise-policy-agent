from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.evaluation.pgvector_ann_models import PgvectorAnnExperimentReport


@dataclass(frozen=True, slots=True)
class PgvectorAnnReportPaths:
    json_path: Path
    markdown_path: Path


def _percentage(value: float) -> str:
    return f"{value * 100:.2f}%"


def render_pgvector_ann_markdown(report: PgvectorAnnExperimentReport) -> str:
    gate = "通过" if report.quality_gate_passed else "未通过"
    lines = [
        "# 企业制度 Agent pgvector HNSW 实验报告",
        "",
        f"- 模式：`{report.evaluation_mode.value}`",
        f"- Embedding：`{report.embedding_provider}`",
        f"- Source collection：`{report.source_collection}`",
        f"- Device / batch：`{report.requested_device or 'auto'}` / {report.embedding_batch_size}",
        f"- Query / judgments：{report.total_cases} / {report.total_judgments}",
        f"- Warm-up / repetitions：{report.warmup_iterations} / {report.measured_repetitions}",
        f"- 安全边界：`{report.security_boundary}`",
        f"- 默认配置门禁：**{gate}**",
        "",
        "## Exact 与 HNSW 对照",
        "",
        "| Backend | 参数 | ANN Recall@5 | Judged Recall@5 | MRR@5 | nDCG@5 | p50 | p95 | Build | Errors | Gate | Pareto |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for point in report.points:
        identity = point.configuration.identity if point.configuration else "exact"
        build = "—" if point.index_build_ms is None else f"{point.index_build_ms:.3f} ms"
        lines.append(
            f"| `{point.backend}` | `{identity}` | {_percentage(point.ann_recall_at_5)} | "
            f"{_percentage(point.judged_recall_at_5)} | {_percentage(point.mrr_at_5)} | "
            f"{_percentage(point.ndcg_at_5)} | {point.p50_ms:.3f} ms | "
            f"{point.p95_ms:.3f} ms | {build} | {point.error_count} | "
            f"{'通过' if point.meets_quality_gate else '未通过'} | "
            f"{'是' if point.pareto_optimal else '否'} |"
        )
    lines.extend(
        (
            "",
            "## 决策边界",
            "",
            "- ANN Recall@5 以 pgvector exact Top-5 为参照；Judged Recall/MRR/nDCG 使用人工 judgments；",
            "- 授权 ID 先复制到隔离实验表，HNSW 图中不存在该身份无权访问的记录；",
            "- 索引构建耗时单独报告，不计入查询 p50/p95；",
            "- Pareto 和默认门禁不会自动修改生产配置；",
            "- Offline Embedding 只验证实验方法，真实 BGE 与固定硬件结果必须单独运行。",
            "",
        )
    )
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_pgvector_ann_report(
    report: PgvectorAnnExperimentReport,
    output_directory: str | Path,
) -> PgvectorAnnReportPaths:
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "pgvector-hnsw-experiment-report.json"
    markdown_path = directory / "pgvector-hnsw-experiment-report.md"
    _atomic_write(json_path, report.model_dump_json(indent=2) + "\n")
    _atomic_write(markdown_path, render_pgvector_ann_markdown(report))
    return PgvectorAnnReportPaths(json_path=json_path, markdown_path=markdown_path)
