from scripts.verify_runtime_observability import run_verification


def test_offline_runtime_observability_verification_passes() -> None:
    report = run_verification()

    assert report["passed"] is True
    assert report["schema_version"] == "1.0"
    assert report["requests_total"] == 3
    assert report["tracked_route_keys"] == 2
    assert report["prometheus_content_type"] == ("text/plain; version=0.0.4; charset=utf-8")
    assert report["network_calls"] is False
    assert report["live_llm_calls"] is False
