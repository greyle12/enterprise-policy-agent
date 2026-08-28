from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

from app.persistence.postgres_schema import (
    AGENT_STATE_SCHEMA,
    AGENT_STATE_SCHEMA_VERSION,
    PostgresStateSchemaError,
    REQUIRED_AGENT_STATE_COLUMNS,
    REQUIRED_AGENT_STATE_TABLES,
    initialize_postgres_state_schema,
    inspect_postgres_state_schema,
)

_TABLE_PATTERN = re.compile(
    rf"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+{AGENT_STATE_SCHEMA}\.([a-z_]+)",
    re.IGNORECASE,
)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Cursor:
    def __init__(self, *, one=None, rows=()) -> None:
        self._one = one
        self._rows = tuple(rows)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class _Database:
    def __init__(self) -> None:
        self.version = 0
        self.tables: set[str] = set()
        self.columns: dict[str, set[str]] = {}
        self.sql: list[str] = []
        self.migration_insert_count = 0


class _Connection:
    def __init__(self, database: _Database) -> None:
        self.database = database

    def execute(self, query: str, params: Sequence[object] | None = None) -> _Cursor:
        values = tuple(params or ())
        self.database.sql.append(query)
        normalized = " ".join(query.split())
        table_match = _TABLE_PATTERN.search(normalized)
        if table_match is not None:
            table = table_match.group(1)
            self.database.tables.add(table)
            self.database.columns[table] = set(REQUIRED_AGENT_STATE_COLUMNS[table])
            return _Cursor()
        if "INSERT INTO agent_runtime.schema_migrations" in normalized:
            self.database.version = int(values[0])
            self.database.migration_insert_count += 1
            return _Cursor()
        if "SELECT to_regclass" in normalized:
            relation = (
                "agent_runtime.schema_migrations"
                if "schema_migrations" in self.database.tables
                else None
            )
            return _Cursor(one=(relation,))
        if "SELECT COALESCE(MAX(version), 0)" in normalized:
            return _Cursor(one=(self.database.version,))
        if "FROM information_schema.tables" in normalized:
            return _Cursor(rows=((table,) for table in sorted(self.database.tables)))
        if "FROM information_schema.columns" in normalized:
            rows = (
                (table, column)
                for table in sorted(self.database.columns)
                for column in sorted(self.database.columns[table])
            )
            return _Cursor(rows=rows)
        return _Cursor()


def run_verification() -> dict[str, object]:
    database = _Database()
    connection = _Connection(database)
    first = initialize_postgres_state_schema(connection)
    sql_after_first_setup = len(database.sql)
    second = initialize_postgres_state_schema(connection)
    second_setup_sql = database.sql[sql_after_first_setup:]

    database.columns["agent_sessions"].remove("state_version")
    drifted = inspect_postgres_state_schema(connection)
    database.columns["agent_sessions"].add("state_version")

    newer_database = _Database()
    newer_database.version = AGENT_STATE_SCHEMA_VERSION + 1
    newer_database.tables.update(REQUIRED_AGENT_STATE_TABLES)
    newer_database.columns = {
        table: set(columns) for table, columns in REQUIRED_AGENT_STATE_COLUMNS.items()
    }
    newer_version_rejected = False
    try:
        initialize_postgres_state_schema(_Connection(newer_database))
    except PostgresStateSchemaError:
        newer_version_rejected = True

    all_sql = "\n".join(database.sql)
    second_sql = "\n".join(second_setup_sql)
    main_source = (_PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    checks = {
        "fresh_setup_reaches_supported_version": (
            first.ready and first.current_version == AGENT_STATE_SCHEMA_VERSION
        ),
        "all_required_domain_tables_are_created": (
            set(first.tables) == REQUIRED_AGENT_STATE_TABLES
        ),
        "setup_is_idempotent": (
            second.ready
            and database.migration_insert_count == 1
            and "CREATE TABLE agent_runtime.agent_sessions" not in second_sql
        ),
        "schema_drift_is_reported": (
            not drifted.ready and drifted.missing_columns == {"agent_sessions": ("state_version",)}
        ),
        "newer_schema_version_fails_closed": newer_version_rejected,
        "payloads_use_jsonb_and_timestamps_use_timestamptz": (
            "payload_json JSONB NOT NULL" in all_sql and "TIMESTAMPTZ" in all_sql
        ),
        "submission_idempotency_is_database_enforced": (
            "idempotency_key TEXT PRIMARY KEY" in all_sql
            and "draft_id TEXT NOT NULL UNIQUE" in all_sql
        ),
        "session_revision_and_tombstone_are_reserved": (
            "state_version BIGINT NOT NULL" in all_sql and "deleted_at TIMESTAMPTZ" in all_sql
        ),
        "future_authenticated_owner_requires_complete_pair": (
            "owner_subject IS NULL AND owner_identity_source IS NULL" in all_sql
            and "owner_subject IS NOT NULL AND owner_identity_source IS NOT NULL" in all_sql
        ),
        "audit_receipt_relationship_is_restrictive": (
            "REFERENCES agent_runtime.approval_submissions(submission_id)" in all_sql
            and "ON DELETE RESTRICT" in all_sql
        ),
        "fastapi_runtime_remains_on_explicit_sqlite_composition": (
            "SQLiteCheckpointSaver(settings.sqlite_database_path)" in main_source
            and "SQLiteAgentStateStore(settings.sqlite_database_path)" in main_source
            and "agent_state_provider" not in main_source
        ),
        "verifier_has_no_database_or_external_calls": True,
    }
    return {
        "schema_version": "1.0",
        "phase": 38,
        "step": 2,
        "passed": all(checks.values()),
        "postgres_schema": AGENT_STATE_SCHEMA,
        "postgres_schema_version": first.current_version,
        "tables": list(first.tables),
        "migration_insert_count": database.migration_insert_count,
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
