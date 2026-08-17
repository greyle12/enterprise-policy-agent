from scripts.verify_provider_backpressure import run_verification


def test_offline_provider_backpressure_verification_passes() -> None:
    report = run_verification()

    assert report["passed"] is True
    assert report["configuration"] == {
        "max_concurrency": 2,
        "max_queue": 2,
        "queue_timeout_seconds": 1,
    }
    assert report["capacity_metrics"] == {
        "requests": 5,
        "accepted": 4,
        "completed": 4,
        "rejected": 1,
        "peak_in_flight": 2,
        "peak_queued": 2,
    }
    assert report["network_calls"] is False
    assert report["live_llm_calls"] is False
