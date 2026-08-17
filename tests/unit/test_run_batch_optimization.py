import json

from scripts.run_batch_optimization import main, run_comparison


def test_run_comparison_returns_offline_report() -> None:
    report = run_comparison(
        item_count=4,
        batch_size=2,
        call_overhead_ms=0.2,
        batch_latency_ms=0.05,
    )

    assert report.quality_gate_passed is True
    assert report.network_calls is False
    assert report.live_model_calls is False


def test_cli_writes_reports_and_prints_summary(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "--items",
            "4",
            "--batch-size",
            "2",
            "--call-overhead-ms",
            "0.2",
            "--batch-latency-ms",
            "0.05",
            "--output-dir",
            str(tmp_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["quality_gate_passed"] is True
    assert payload["scenarios"]["embedding_documents"]["batched_provider_calls"] == 1
    assert (tmp_path / "agent-batch-optimization-report.json").is_file()
    assert (tmp_path / "agent-batch-optimization-report.md").is_file()


def test_cli_rejects_invalid_item_count(capsys) -> None:
    try:
        main(["--items", "0"])
    except SystemExit as exc:
        assert exc.code == 2
    assert "必须是大于零的整数" in capsys.readouterr().err
