from __future__ import annotations

import json

from scripts.verify_policy_research import main, run_verification


async def test_offline_policy_research_verification_passes() -> None:
    report = await run_verification()

    assert report["passed"] is True
    assert report["assistant_version"] == "1.0"
    assert report["local_only"] == {
        "status": "completed",
        "web_status": "not_requested",
        "web_executed": False,
    }
    assert report["hybrid"] == {
        "status": "completed",
        "web_status": "completed",
        "internal_sources": ["S1"],
        "external_sources": ["W1"],
        "query_redacted": True,
        "web_attempts": 3,
        "recovered": True,
    }
    assert all(report["checks"].values())


def test_policy_research_cli_prints_json(capsys) -> None:
    exit_code = main()

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
