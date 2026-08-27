from scripts.verify_retrieval_candidate_sweep import run_verification


def test_candidate_window_verification_passes_offline() -> None:
    report = run_verification()

    assert report["phase"] == 33
    assert report["passed"] is True
    assert report["case_count"] == 20
    assert report["judgment_count"] == 30
    assert report["candidate_ks"] == (5, 10, 20, 40)
    assert report["default_candidate_k"] == 20
    assert report["point_count"] == 8
    assert report["network_calls"] is False
    assert report["external_model_calls"] is False
    assert all(report["checks"].values())
