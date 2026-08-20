from __future__ import annotations

from scripts.verify_bm25_retrieval import main, run_verification


def test_bm25_retrieval_verification_passes() -> None:
    report = run_verification()

    assert report["phase"] == 26
    assert report["passed"] is True
    assert report["document_count"] == 5
    assert report["chunk_count"] == 199
    assert report["keyword_index_size"] == 199
    assert report["network_calls"] is False
    assert report["model_calls"] is False
    assert report["verification_scope"] == "bm25_channel_only"
    assert all(report["checks"].values())


def test_bm25_retrieval_cli_prints_json(capsys) -> None:
    exit_code = main()

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"phase": 26' in output
    assert '"passed": true' in output
