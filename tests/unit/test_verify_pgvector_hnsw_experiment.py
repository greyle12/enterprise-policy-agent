from scripts.verify_pgvector_hnsw_experiment import run_verification


def test_phase_34_verification_passes_without_database_or_models() -> None:
    report = run_verification()

    assert report["phase"] == 34
    assert report["passed"] is True
    assert report["case_count"] == 20
    assert report["judgment_count"] == 30
    assert report["point_count"] == 3
    assert report["default_configuration"] == "m16-efc64-efs40"
    assert report["database_calls"] is False
    assert report["external_model_calls"] is False
    assert all(report["checks"].values())
