from __future__ import annotations

import json

from scripts.verify_concurrency_load import main, run_verification


def test_offline_concurrency_load_verification_passes() -> None:
    report = run_verification()

    assert report["passed"] is True
    assert report["schema_version"] == "1.0"
    assert report["request_count"] == 24
    assert report["configured_concurrency"] == 12
    assert report["network_calls"] is False
    assert report["live_llm_calls"] is False
    assert len(report["scenarios"]) == 3
    assert all(report["checks"].values())


def test_concurrency_load_verification_cli_prints_json(capsys) -> None:
    exit_code = main()

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["passed"] is True
