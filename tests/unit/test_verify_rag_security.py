from scripts.verify_rag_security import run_verification


def test_offline_rag_security_verification_passes() -> None:
    report = run_verification()

    assert report["passed"] is True
    assert report["schema_version"] == "1.0"
    assert report["rule_set_version"] == "day29-v1"
    assert report["permission_denial_accuracy"] == 1.0
    assert report["prompt_injection_block_accuracy"] == 1.0
    assert report["benign_allow_accuracy"] == 1.0
    assert report["provider_calls"] == 0
    assert report["network_calls"] is False
    assert report["live_llm_calls"] is False
