from scripts.verify_multi_instance_state_schema import run_verification


def test_offline_multi_instance_state_schema_verification_passes() -> None:
    result = run_verification()

    assert result["phase"] == 38
    assert result["step"] == 2
    assert result["passed"] is True
    assert result["postgres_schema"] == "agent_runtime"
    assert result["postgres_schema_version"] == 1
    assert result["runtime_backend_switched"] is False
    assert result["sqlite_data_migrated"] is False
    assert result["database_calls"] is False
    assert result["external_calls"] is False
    assert all(result["checks"].values())
