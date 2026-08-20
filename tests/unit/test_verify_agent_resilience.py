from __future__ import annotations

from scripts.verify_agent_resilience import run_verification


def test_day20_resilience_verification_passes() -> None:
    result = run_verification()

    assert result["passed"] is True
    assert result["workflow_version"] == "1.5"
    assert result["read_only_recovery"] == {
        "tool": "policy_answer",
        "attempts": 3,
        "outcome": "recovered",
    }
    assert result["safe_degradation"] == {
        "status": "unavailable",
        "attempts": 3,
        "error_code": "tool_upstream_unavailable",
        "sensitive_error_exposed": False,
    }
    assert result["mutation_guard"] == {
        "tool": "approval_submission",
        "attempts": 1,
        "max_attempts": 1,
        "retry_safe": False,
        "recovery_action": "resubmit_with_same_session",
    }
