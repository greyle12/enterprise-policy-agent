from __future__ import annotations

import json
from pathlib import Path

from scripts.run_performance_benchmark import main


def test_performance_cli_writes_reports_and_prints_summary(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code = main(
        [
            "--warmups",
            "0",
            "--iterations",
            "1",
            "--output-dir",
            str(tmp_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["quality_gate_passed"] is True
    assert payload["network_calls"] is False
    assert len(payload["scenarios"]) == 5
    assert (tmp_path / "agent-performance-report.json").is_file()
    assert (tmp_path / "agent-performance-report.md").is_file()
