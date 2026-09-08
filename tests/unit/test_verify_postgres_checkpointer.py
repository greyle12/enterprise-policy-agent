from scripts.verify_postgres_checkpointer import run_verification


def test_postgres_checkpointer_offline_verification_passes() -> None:
    result = run_verification()

    assert result["phase"] == 38
    assert result["step"] == 4
    assert result["status"] == "checkpointer_backend_ready"
    assert result["passed"] is True
    assert result["official_saver"] == "AsyncPostgresSaver"
    assert result["checkpoint_schema"] == "agent_runtime"
    assert result["runtime_backend_switched"] is False
    assert result["sqlite_data_migrated"] is False
    assert result["database_calls"] is False
    assert result["external_calls"] is False
    assert all(result["checks"].values())
