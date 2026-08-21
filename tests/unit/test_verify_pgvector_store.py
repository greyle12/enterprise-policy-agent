from scripts.verify_pgvector_store import main, run_verification


def test_pgvector_store_verification_passes() -> None:
    report = run_verification()

    assert report["phase"] == 29
    assert report["passed"] is True
    assert report["database_calls"] is False
    assert report["network_calls"] is False
    assert report["search_mode"] == "exact_cosine"
    assert report["persisted_record_count"] == 2
    assert all(report["checks"].values())


def test_pgvector_store_cli_prints_json(capsys) -> None:
    assert main() == 0

    output = capsys.readouterr().out
    assert '"phase": 29' in output
    assert '"passed": true' in output
