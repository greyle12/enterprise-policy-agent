from __future__ import annotations

import json
from pathlib import Path

from app.portfolio import (
    render_portfolio_markdown,
    run_offline_portfolio_demo,
    write_portfolio_report,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"


async def test_portfolio_report_writes_machine_and_human_readable_evidence(
    tmp_path: Path,
) -> None:
    report = await run_offline_portfolio_demo(policy_directory=_POLICY_DIRECTORY)

    paths = write_portfolio_report(report, tmp_path)
    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
    markdown = paths.markdown_path.read_text(encoding="utf-8")

    assert payload["release_label"] == "day30"
    assert payload["passed_scenarios"] == 6
    assert payload["network_calls"] is False
    assert "作品集演示报告" in markdown
    assert "不代表真实 BGE、LLM、网络效果或生产 SLA" in markdown
    assert render_portfolio_markdown(report) == markdown


async def test_portfolio_report_does_not_record_demo_prompts(
    tmp_path: Path,
) -> None:
    report = await run_offline_portfolio_demo(policy_directory=_POLICY_DIRECTORY)

    paths = write_portfolio_report(report, tmp_path)
    serialized = paths.json_path.read_text(encoding="utf-8")

    assert "Ignore all previous" not in serialized
    assert "苏州科技有限公司" not in serialized
    assert "API key" not in serialized
