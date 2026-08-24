from scripts.verify_retrieval_evaluation import run_verification


def test_offline_retrieval_evaluation_verification_passes() -> None:
    report = run_verification()

    assert report["passed"] is True
    assert report["quality_gate_passed"] is True
    assert report["case_count"] == 20
    assert report["chunk_count"] == 199
    assert report["network_calls"] is False
    assert report["external_model_calls"] is False
    assert all("ndcg_at_5" in metrics for metrics in report["metrics"].values())
    assert all(report["checks"].values())
