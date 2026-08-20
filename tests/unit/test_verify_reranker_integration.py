from __future__ import annotations

from scripts.verify_reranker_integration import main, run_verification


def test_reranker_integration_verification_passes() -> None:
    report = run_verification()

    assert report["phase"] == 28
    assert report["passed"] is True
    assert report["document_count"] == 5
    assert report["chunk_count"] == 199
    assert report["vector_index_size"] == 199
    assert report["bm25_index_size"] == 199
    assert report["rerank_candidate_k"] == 20
    assert report["configured_bge_model"] == "BAAI/bge-reranker-v2-m3"
    assert report["runtime_provider"] == "offline_lexical_fixture"
    assert report["network_calls"] is False
    assert report["external_model_calls"] is False
    assert report["real_bge_model_loaded"] is False
    assert all(report["checks"].values())


def test_reranker_integration_cli_prints_json(capsys) -> None:
    exit_code = main()

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"phase": 28' in output
    assert '"passed": true' in output
