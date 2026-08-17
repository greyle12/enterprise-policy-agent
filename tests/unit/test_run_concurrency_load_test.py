from __future__ import annotations

import json
from pathlib import Path

from scripts.run_concurrency_load_test import main


def test_concurrency_load_cli_writes_reports_and_prints_summary(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code = main(
        [
            "--requests",
            "6",
            "--concurrency",
            "3",
            "--provider-latency-ms",
            "1",
            "--output-dir",
            str(tmp_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["quality_gate_passed"] is True
    assert payload["network_calls"] is False
    assert payload["live_llm_calls"] is False
    assert len(payload["scenarios"]) == 3
    assert (tmp_path / "agent-concurrency-load-report.json").is_file()
    assert (tmp_path / "agent-concurrency-load-report.md").is_file()
