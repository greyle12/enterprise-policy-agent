from scripts.verify_document_indexing import run_verification


def test_offline_document_indexing_verification_passes() -> None:
    report = run_verification()

    assert report["passed"] is True
    assert report["database_calls"] is False
    assert report["network_calls"] is False
    assert report["no_op_embedding_count"] == 0
    assert report["incremental_upsert_count"] == 1
    assert report["stale_delete_count"] == 1
    assert all(report["checks"].values())
