from __future__ import annotations

from pathlib import Path

from scripts.run_retrieval_evaluation import main


def test_offline_cli_writes_reports_and_passes_quality_gate(tmp_path: Path, capsys) -> None:
    exit_code = main(["--mode", "offline", "--output-dir", str(tmp_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"quality_gate_passed": true' in output
    assert '"corpus_chunks": 199' in output
    assert '"ndcg_at_5"' in output
    assert (tmp_path / "retrieval-evaluation-report.json").is_file()
    assert (tmp_path / "retrieval-evaluation-report.md").is_file()


def test_cli_returns_quality_failure_exit_code_for_impossible_threshold(
    tmp_path: Path, capsys
) -> None:
    exit_code = main(
        [
            "--mode",
            "offline",
            "--output-dir",
            str(tmp_path),
            "--minimum-recall-at-5",
            "1.0",
        ]
    )

    assert exit_code == 1
    assert '"quality_gate_passed": false' in capsys.readouterr().out


def test_cli_applies_ndcg_quality_threshold(tmp_path: Path, capsys) -> None:
    exit_code = main(
        [
            "--mode",
            "offline",
            "--output-dir",
            str(tmp_path),
            "--minimum-ndcg-at-5",
            "1.0",
        ]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"ndcg_at_5"' in output
    assert '"quality_gate_passed": false' in output
