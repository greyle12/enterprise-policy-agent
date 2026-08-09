from __future__ import annotations

from pathlib import Path

from scripts.run_golden_evaluation import main


def test_offline_cli_writes_both_reports(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code = main(
        [
            "--mode",
            "offline",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "golden-evaluation-report.json").is_file()
    assert (tmp_path / "golden-evaluation-report.md").is_file()
    assert '"quality_gate_passed": true' in capsys.readouterr().out
