from scripts.verify_embedding_reranker_batching import main, run_verification


def test_day26_verification_passes_all_offline_contracts() -> None:
    result = run_verification()

    assert result["passed"] is True
    assert result["network_calls"] is False
    assert result["live_model_calls"] is False
    assert all(result["checks"].values())


def test_day26_verification_cli_returns_success(capsys) -> None:
    exit_code = main()

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"passed": true' in output
    assert '"batched_provider_calls": 1' in output
