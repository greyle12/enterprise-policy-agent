from scripts.verify_indexing_lease import run_verification


def test_phase_36_indexing_lease_verification_passes_offline() -> None:
    report = run_verification()

    assert report["phase"] == 36
    assert report["passed"] is True
    assert report["latest_fencing_token"] == 2
    assert report["database_calls"] is False
    assert report["external_model_calls"] is False
    assert all(report["checks"].values())
