from scripts.verify_graded_relevance import run_verification


def test_offline_graded_relevance_verification_passes() -> None:
    report = run_verification()

    assert report["phase"] == 32
    assert report["passed"] is True
    assert report["case_count"] == 20
    assert report["judgment_count"] == 30
    assert report["observed_relevance_grades"] == [1, 2, 3]
    assert report["ideal_ndcg_at_3"] == 1.0
    assert report["reversed_ndcg_at_3"] < 1.0
    assert report["network_calls"] is False
    assert report["external_model_calls"] is False
    assert all(report["checks"].values())
