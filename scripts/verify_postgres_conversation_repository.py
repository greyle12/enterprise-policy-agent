from __future__ import annotations

import json
from pathlib import Path

from app.persistence.postgres_schema import AGENT_STATE_SCHEMA_VERSION

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_verification() -> dict[str, object]:
    repository_source = (_PROJECT_ROOT / "app" / "persistence" / "postgres_memory.py").read_text(
        encoding="utf-8"
    )
    main_source = (_PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")

    sanitize_position = repository_source.index("sanitize_memory_content(user_message)")
    transaction_position = repository_source.index("async with self._pool.connection()")
    lock_position = repository_source.index("FROM {AGENT_STATE_SCHEMA}.agent_sessions")
    allocate_position = repository_source.index("SELECT COALESCE(MAX(turn_number), 0) + 1")
    checks = {
        "repository_uses_injected_async_pool": (
            "pool: PostgresStateConnectionPool" in repository_source
            and "AsyncConnectionPool(" not in repository_source
        ),
        "content_is_sanitized_before_database_access": (
            sanitize_position < transaction_position
            and "sanitize_memory_content(\n            assistant_message" in repository_source
        ),
        "session_row_serializes_turn_allocation": (
            lock_position < allocate_position
            and "FOR UPDATE" in repository_source[lock_position:allocate_position]
            and "conversation turn cannot be appended to a tombstoned session" in repository_source
        ),
        "user_and_assistant_are_inserted_as_one_guarded_pair": (
            "INSERT INTO {AGENT_STATE_SCHEMA}.conversation_messages" in repository_source
            and "ON CONFLICT DO NOTHING" in repository_source
            and "if len(inserted) != 2:" in repository_source
        ),
        "retention_deletes_only_complete_older_turns": (
            "oldest_retained_turn = turn_number - self.max_stored_turns + 1" in repository_source
            and "WHERE session_id = %s AND turn_number < %s" in repository_source
        ),
        "snapshot_is_single_statement_and_preserves_sqlite_ordering": (
            "COUNT(*) OVER () AS total_message_count" in repository_source
            and "ROW_NUMBER() OVER" in repository_source
            and "CASE message.role" in repository_source
            and "ORDER BY recency_rank DESC" in repository_source
        ),
        "tombstoned_session_messages_are_hidden": (
            "session.deleted_at IS NULL" in repository_source
        ),
        "clear_is_session_scoped_and_serialized": (
            repository_source.count("FOR UPDATE") >= 2
            and "DELETE FROM {AGENT_STATE_SCHEMA}.conversation_messages" in repository_source
            and "DELETE FROM {AGENT_STATE_SCHEMA}.agent_sessions" not in repository_source
        ),
        "fastapi_runtime_remains_on_sqlite_memory": (
            "SQLiteConversationMemoryStore(settings.sqlite_database_path)" in main_source
            and "PostgresConversationMemoryStore" not in main_source
            and "agent_state_provider" not in main_source
        ),
        "verifier_has_no_database_or_external_calls": True,
    }
    return {
        "schema_version": "1.0",
        "phase": 38,
        "step": 3,
        "substep": 2,
        "status": "repository_only",
        "passed": all(checks.values()),
        "postgres_schema_version": AGENT_STATE_SCHEMA_VERSION,
        "repository": "PostgresConversationMemoryStore",
        "domains": ["conversation"],
        "completed_repository_domains": [
            "session",
            "application_draft",
            "conversation",
        ],
        "runtime_backend_switched": False,
        "sqlite_data_migrated": False,
        "database_calls": False,
        "external_calls": False,
        "checks": checks,
    }


def main() -> int:
    result = run_verification()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
