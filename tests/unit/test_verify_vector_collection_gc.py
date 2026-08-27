from scripts.verify_vector_collection_gc import run_verification


def test_offline_vector_collection_gc_verification_passes() -> None:
    result = run_verification()

    assert result["phase"] == 37
    assert result["passed"] is True
    assert result["deleted_record_count"] == 6
    assert result["database_calls"] is False
    assert result["external_model_calls"] is False
    assert all(result["checks"].values())
