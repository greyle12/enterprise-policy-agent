from __future__ import annotations

import json
from pathlib import Path

from scripts.run_portfolio_demo import main


def test_portfolio_cli_writes_reports_and_prints_summary(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code = main(["--output-dir", str(tmp_path)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["quality_gate_passed"] is True
    assert payload["release_label"] == "day30"
    assert payload["passed_scenarios"] == 6
    assert payload["network_calls"] is False
    assert payload["live_llm_calls"] is False
    assert set(payload["scenarios"]) == {
        "rag_citation",
        "material_rules",
        "approval_route",
        "human_in_loop",
        "research_boundary",
        "security_boundary",
    }
    assert (tmp_path / "portfolio-demo-report.json").is_file()
    assert (tmp_path / "portfolio-demo-report.md").is_file()
