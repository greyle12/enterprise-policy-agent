from __future__ import annotations

import json

from scripts.verify_portfolio_release import main, run_verification


def test_checked_in_portfolio_release_passes_all_contracts() -> None:
    report = run_verification()

    assert report["passed"] is True
    assert report["portfolio_scenarios"] == 6
    assert report["portfolio_scenarios_passed"] == 6
    assert report["policy_documents"] == 5
    assert report["application_samples"] == 6
    assert report["golden_cases"] == 30
    assert report["network_calls"] is False
    assert report["live_llm_calls"] is False
    assert all(report["checks"].values())


def test_portfolio_release_cli_prints_json(capsys) -> None:
    exit_code = main()

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["passed"] is True
    assert payload["release_label"] == "day30"
