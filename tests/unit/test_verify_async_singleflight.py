from scripts.verify_async_singleflight import run_verification


def test_offline_async_singleflight_verification_passes() -> None:
    report = run_verification()

    assert report["passed"] is True
    assert report["concurrent_requests"] == 12
    assert report["upstream_calls"] == 1
    assert report["coalesced_requests"] == 11
    assert report["cache_writes"] == 1
    assert report["network_calls"] is False
    assert report["live_llm_calls"] is False
