from __future__ import annotations

import json
from pathlib import Path

from scripts.profile_agent_performance import main


def test_cprofile_cli_writes_raw_and_structured_reports(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code = main(
        [
            "--warmups",
            "0",
            "--iterations",
            "1",
            "--top",
            "5",
            "--output-dir",
            str(tmp_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["quality_gate_passed"] is True
    assert payload["profiler"] == "cProfile"
    assert payload["hotspot_count"] == 5
    assert (tmp_path / "agent-performance.cprofile").is_file()
    assert (tmp_path / "agent-performance-report.json").is_file()
    assert (tmp_path / "agent-cprofile-hotspots.json").is_file()
    assert (tmp_path / "agent-cprofile-hotspots.md").is_file()
