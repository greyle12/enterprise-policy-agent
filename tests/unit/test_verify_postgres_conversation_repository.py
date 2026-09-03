from scripts.verify_postgres_conversation_repository import run_verification


def test_offline_postgres_conversation_repository_verification_passes() -> None:
    result = run_verification()

    assert result["phase"] == 38
    assert result["step"] == 3
    assert result["substep"] == 2
    assert result["status"] == "repository_only"
    assert result["passed"] is True
    assert result["postgres_schema_version"] == 1
    assert result["repository"] == "PostgresConversationMemoryStore"
    assert result["domains"] == ["conversation"]
    assert result["completed_repository_domains"] == [
        "session",
        "application_draft",
        "conversation",
    ]
    assert result["runtime_backend_switched"] is False
    assert result["sqlite_data_migrated"] is False
    assert result["database_calls"] is False
    assert result["external_calls"] is False
    assert all(result["checks"].values())
