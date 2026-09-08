from scripts.verify_postgres_repository_integration_gate import run_verification


def test_postgres_repository_integration_gate_is_ready_without_database_calls() -> None:
    result = run_verification()

    assert result["phase"] == 38
    assert result["step"] == 3
    assert result["substep"] == 4
    assert result["status"] == "integration_gate_ready"
    assert result["passed"] is True
    assert result["real_database_execution_required"] is True
    assert result["real_database_execution_deferred_to_ci"] is True
    assert result["runtime_backend_switched"] is False
    assert result["sqlite_data_migrated"] is False
    assert result["langgraph_checkpoint_backend_switched"] is False
    assert result["checks"]["windows_psycopg_selector_loop"] is True
    assert all(result["checks"].values())
