from __future__ import annotations

import json

from scripts.verify_agent_performance import main, run_verification


def test_offline_performance_verification_passes() -> None:
    report = run_verification()

    assert report["passed"] is True
    assert report["schema_version"] == "1.0"
    assert report["warmup_iterations"] == 1
    assert report["measured_iterations"] == 3
    assert report["network_calls"] is False
    assert report["live_llm_calls"] is False
    assert report["quality_gate_passed"] is True
    assert len(report["scenario_p95_ms"]) == 5
    assert report["profile"]["hotspot_count"] == 15
    assert all(report["checks"].values())


def test_performance_verification_cli_prints_json(capsys) -> None:
    exit_code = main()

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["passed"] is True
