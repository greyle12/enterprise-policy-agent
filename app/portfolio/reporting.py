from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.portfolio.models import PortfolioDemoReport


@dataclass(frozen=True, slots=True)
class PortfolioReportPaths:
    json_path: Path
    markdown_path: Path


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _table_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_portfolio_markdown(report: PortfolioDemoReport) -> str:
    gate = "通过" if report.quality_gate_passed else "未通过"
    lines = [
        "# 企业制度 Agent Day 30 作品集演示报告",
        "",
        f"- 发布标签：`{report.release_label}`",
        f"- 生成时间：`{report.generated_at.isoformat()}`",
        f"- 制度文档：{report.policy_documents}",
        f"- 演示场景：{report.passed_scenarios}/{report.total_scenarios}",
        f"- 质量门禁：**{gate}**",
        "",
        "> 本报告完全离线。Hash 词法向量、固定 LLM 返回和固定 Web 结果只用于演示",
        "> 编排、规则、安全与引用契约，不代表真实 BGE、LLM、网络效果或生产 SLA。",
        "",
        "## 场景结果",
        "",
        "| 场景 | 能力 | 耗时 | 观测证据 | 结果 |",
        "|---|---|---:|---|---|",
    ]
    for result in report.scenarios:
        status = "通过" if result.passed else f"未通过（{result.error_type or 'contract'}）"
        observations = json.dumps(
            result.observations,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        lines.append(
            f"| `{result.scenario.value}` / {_table_cell(result.title)} | "
            f"{_table_cell(result.capability)} | {result.duration_ms:.3f} ms | "
            f"`{_table_cell(observations)}` | {status} |"
        )
    lines.extend(
        (
            "",
            "## 演示边界",
            "",
            "- 演示复用真实制度解析、检索器、LangGraph、业务规则和安全边界；",
            "- Embedding、LLM 和 Web Search 使用确定性离线夹具，不联网也不读取 API Key；",
            "- 模拟提交只写入进程内存，不会连接真实 OA、ERP 或审批系统；",
            "- 真实效果应另行使用经过授权的 Provider、身份系统和生产流量评测。",
            "",
        )
    )
    return "\n".join(lines)


def write_portfolio_report(
    report: PortfolioDemoReport,
    output_directory: str | Path,
) -> PortfolioReportPaths:
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "portfolio-demo-report.json"
    markdown_path = directory / "portfolio-demo-report.md"
    _atomic_write(json_path, report.model_dump_json(indent=2) + "\n")
    _atomic_write(markdown_path, render_portfolio_markdown(report))
    return PortfolioReportPaths(json_path=json_path, markdown_path=markdown_path)
