from __future__ import annotations

from scripts.verify_hybrid_search import main, run_verification


def test_hybrid_search_verification_passes() -> None:
    report = run_verification()

    assert report["phase"] == 27
    assert report["passed"] is True
    assert report["document_count"] == 5
    assert report["chunk_count"] == 199
    assert report["vector_index_size"] == 199
    assert report["bm25_index_size"] == 199
    assert report["rrf_rank_constant"] == 60
    assert report["network_calls"] is False
    assert report["external_model_calls"] is False
    assert report["verification_scope"] == "hybrid_rrf_without_reranker"
    assert all(report["checks"].values())


def test_hybrid_search_cli_prints_json(capsys) -> None:
    exit_code = main()

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"phase": 27' in output
    assert '"passed": true' in output
