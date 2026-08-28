from __future__ import annotations

import argparse
import re
from collections.abc import Sequence

import pytest

from app.persistence.postgres_schema import (
    AGENT_STATE_SCHEMA,
    AGENT_STATE_SCHEMA_VERSION,
    PostgresStateSchemaError,
    PostgresStateSchemaStatus,
    REQUIRED_AGENT_STATE_COLUMNS,
    REQUIRED_AGENT_STATE_TABLES,
    initialize_postgres_state_schema,
    inspect_postgres_state_schema,
)
from scripts import manage_agent_state_schema

_TABLE_PATTERN = re.compile(
    rf"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+{AGENT_STATE_SCHEMA}\.([a-z_]+)",
    re.IGNORECASE,
)


class _Cursor:
    def __init__(self, *, one=None, rows=()) -> None:
        self._one = one
        self._rows = tuple(rows)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, *, version: int = 0) -> None:
        self.version = version
        self.tables: set[str] = set()
        self.columns: dict[str, set[str]] = {}
        self.sql: list[str] = []
        if version:
            self.tables.update(REQUIRED_AGENT_STATE_TABLES)
            self.columns = {
                table: set(columns) for table, columns in REQUIRED_AGENT_STATE_COLUMNS.items()
            }

    def execute(self, query: str, params: Sequence[object] | None = None) -> _Cursor:
        values = tuple(params or ())
        self.sql.append(query)
        normalized = " ".join(query.split())
        match = _TABLE_PATTERN.search(normalized)
        if match is not None:
            table = match.group(1)
            self.tables.add(table)
            self.columns[table] = set(REQUIRED_AGENT_STATE_COLUMNS[table])
            return _Cursor()
        if "INSERT INTO agent_runtime.schema_migrations" in normalized:
            self.version = int(values[0])
            return _Cursor()
        if "SELECT to_regclass" in normalized:
            relation = (
                "agent_runtime.schema_migrations" if "schema_migrations" in self.tables else None
            )
            return _Cursor(one=(relation,))
        if "SELECT COALESCE(MAX(version), 0)" in normalized:
            return _Cursor(one=(self.version,))
        if "FROM information_schema.tables" in normalized:
            return _Cursor(rows=((table,) for table in sorted(self.tables)))
        if "FROM information_schema.columns" in normalized:
            rows = (
                (table, column)
                for table in sorted(self.columns)
                for column in sorted(self.columns[table])
            )
            return _Cursor(rows=rows)
        return _Cursor()


def _ready_status() -> PostgresStateSchemaStatus:
    return PostgresStateSchemaStatus(
        schema_name=AGENT_STATE_SCHEMA,
        supported_version=AGENT_STATE_SCHEMA_VERSION,
        current_version=AGENT_STATE_SCHEMA_VERSION,
        tables=tuple(sorted(REQUIRED_AGENT_STATE_TABLES)),
        missing_tables=(),
        missing_columns={},
    )


def test_initializes_versioned_agent_state_schema_idempotently() -> None:
    connection = _Connection()

    first = initialize_postgres_state_schema(connection)
    first_domain_create_count = sum(
        "CREATE TABLE agent_runtime.agent_" in query
        or "CREATE TABLE agent_runtime.application_" in query
        or "CREATE TABLE agent_runtime.conversation_" in query
        or "CREATE TABLE agent_runtime.approval_" in query
        or "CREATE TABLE agent_runtime.submission_" in query
        for query in connection.sql
    )
    second = initialize_postgres_state_schema(connection)
    total_domain_create_count = sum(
        "CREATE TABLE agent_runtime.agent_" in query
        or "CREATE TABLE agent_runtime.application_" in query
        or "CREATE TABLE agent_runtime.conversation_" in query
        or "CREATE TABLE agent_runtime.approval_" in query
        or "CREATE TABLE agent_runtime.submission_" in query
        for query in connection.sql
    )

    assert first.ready is True
    assert second.ready is True
    assert first.current_version == 1
    assert set(first.tables) == REQUIRED_AGENT_STATE_TABLES
    assert first_domain_create_count == 5
    assert total_domain_create_count == first_domain_create_count


def test_inspect_reports_an_uninitialized_database_without_mutation() -> None:
    connection = _Connection()

    status = inspect_postgres_state_schema(connection)

    assert status.initialized is False
    assert status.ready is False
    assert status.current_version == 0
    assert set(status.missing_tables) == REQUIRED_AGENT_STATE_TABLES
    assert all("CREATE" not in query for query in connection.sql)


def test_setup_rejects_newer_schema_version() -> None:
    connection = _Connection(version=AGENT_STATE_SCHEMA_VERSION + 1)

    with pytest.raises(PostgresStateSchemaError, match="newer than this application"):
        initialize_postgres_state_schema(connection)


def test_setup_rejects_missing_required_column_at_current_version() -> None:
    connection = _Connection(version=AGENT_STATE_SCHEMA_VERSION)
    connection.columns["agent_sessions"].remove("state_version")

    with pytest.raises(PostgresStateSchemaError, match="missing_columns"):
        initialize_postgres_state_schema(connection)


def test_manage_schema_command_uses_agent_dsn_without_exposing_it() -> None:
    captured: dict[str, object] = {}

    class _Manager:
        def setup(self) -> PostgresStateSchemaStatus:
            captured["setup"] = True
            return _ready_status()

        def status(self) -> PostgresStateSchemaStatus:
            raise AssertionError("status must not run for setup")

    def factory(dsn: str, *, connect_timeout_seconds: float):
        captured["dsn"] = dsn
        captured["timeout"] = connect_timeout_seconds
        return _Manager()

    result = manage_agent_state_schema._run(
        argparse.Namespace(
            command="setup",
            dsn="postgresql://agent:secret@postgres.example:5432/policy_agent",
            connect_timeout_seconds=7.0,
        ),
        manager_factory=factory,
    )

    assert result["passed"] is True
    assert result["runtime_backend_switched"] is False
    assert result["sqlite_data_migrated"] is False
    assert captured == {
        "dsn": "postgresql://agent:secret@postgres.example:5432/policy_agent",
        "timeout": 7.0,
        "setup": True,
    }
    assert "secret" not in str(result)


def test_manage_schema_cli_requires_an_explicit_or_environment_dsn(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("AGENT_POSTGRES_DSN", raising=False)

    exit_code = manage_agent_state_schema.main(["status"])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert '"passed": false' in output
    assert "AGENT_POSTGRES_DSN is required" in output
