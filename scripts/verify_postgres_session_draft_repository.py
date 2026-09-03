from __future__ import annotations

import json
from pathlib import Path

from app.persistence.postgres_schema import AGENT_STATE_SCHEMA_VERSION

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_verification() -> dict[str, object]:
    repository_source = (_PROJECT_ROOT / "app" / "persistence" / "postgres_runtime.py").read_text(
        encoding="utf-8"
    )
    connection_source = (
        _PROJECT_ROOT / "app" / "persistence" / "postgres_connection.py"
    ).read_text(encoding="utf-8")
    main_source = (_PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")

    checks = {
        "async_pool_is_injected_not_constructed_by_repository": (
            "class PostgresStateConnectionPool(Protocol)" in connection_source
            and "pool: PostgresStateConnectionPool" in repository_source
            and "AsyncConnectionPool(" not in repository_source
        ),
        "session_and_draft_share_one_transaction_context": (
            "async def save_route_state(" in repository_source
            and "async with self._pool.connection() as connection:" in repository_source
            and "await self._save_session_head(connection" in repository_source
            and "await self._save_draft_revision(" in repository_source
        ),
        "session_insert_is_race_safe": (
            "ON CONFLICT (session_id) DO NOTHING" in repository_source
            and "WHERE session_id = %s\n            FOR UPDATE" in repository_source
        ),
        "session_updates_use_state_version_cas": (
            "state_version = state_version + 1" in repository_source
            and "AND state_version = %s" in repository_source
            and "session state_version CAS update was lost" in repository_source
        ),
        "session_tombstones_cannot_be_revived": (
            "tombstoned session cannot be revived" in repository_source
            and "AND deleted_at IS NULL" in repository_source
        ),
        "draft_insert_and_lifecycle_update_are_guarded": (
            "ON CONFLICT (draft_id, revision) DO NOTHING" in repository_source
            and "draft business content cannot change inside an existing revision"
            in repository_source
            and "draft lifecycle CAS update was lost" in repository_source
        ),
        "reads_hide_tombstoned_sessions_and_sort_revisions": (
            "session.deleted_at IS NULL" in repository_source
            and "ORDER BY draft.revision DESC" in repository_source
            and "ORDER BY draft.revision" in repository_source
        ),
        "delete_is_soft_for_session_and_scoped_for_drafts": (
            "SET deleted_at = %s" in repository_source
            and "DELETE FROM {AGENT_STATE_SCHEMA}.application_draft_snapshots" in repository_source
            and "DELETE FROM {AGENT_STATE_SCHEMA}.agent_sessions" not in repository_source
        ),
        "repository_requires_exact_schema_version": (
            "current_version != AGENT_STATE_SCHEMA_VERSION" in repository_source
        ),
        "fastapi_runtime_remains_on_explicit_sqlite_composition": (
            "SQLiteCheckpointSaver(settings.sqlite_database_path)" in main_source
            and "SQLiteAgentStateStore(settings.sqlite_database_path)" in main_source
            and "PostgresAgentStateStore" not in main_source
            and "agent_state_provider" not in main_source
        ),
        "verifier_has_no_database_or_external_calls": True,
    }
    return {
        "schema_version": "1.0",
        "phase": 38,
        "step": 3,
        "substep": 1,
        "status": "repository_only",
        "passed": all(checks.values()),
        "postgres_schema_version": AGENT_STATE_SCHEMA_VERSION,
        "repository": "PostgresAgentStateStore",
        "domains": ["session", "application_draft"],
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
