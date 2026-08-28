from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from typing import Any, Protocol, Self

AGENT_STATE_SCHEMA = "agent_runtime"
AGENT_STATE_SCHEMA_VERSION = 1
AGENT_STATE_MIGRATION_TABLE = f"{AGENT_STATE_SCHEMA}.schema_migrations"

REQUIRED_AGENT_STATE_COLUMNS: dict[str, frozenset[str]] = {
    "schema_migrations": frozenset({"version", "description", "applied_at"}),
    "agent_sessions": frozenset(
        {
            "session_id",
            "owner_subject",
            "owner_identity_source",
            "turn_number",
            "phase",
            "active_draft_id",
            "draft_revision",
            "pending_confirmation",
            "checkpoint_backend",
            "state_version",
            "deleted_at",
            "created_at",
            "updated_at",
        }
    ),
    "application_draft_snapshots": frozenset(
        {
            "draft_id",
            "revision",
            "session_id",
            "status",
            "payload_json",
            "created_at",
            "updated_at",
        }
    ),
    "conversation_messages": frozenset(
        {
            "message_id",
            "session_id",
            "turn_number",
            "role",
            "content",
            "redacted",
            "truncated",
            "created_at",
        }
    ),
    "approval_submissions": frozenset(
        {
            "idempotency_key",
            "submission_id",
            "draft_id",
            "session_id",
            "employee_id",
            "payload_json",
            "created_at",
        }
    ),
    "submission_audit_records": frozenset(
        {
            "audit_id",
            "submission_id",
            "draft_id",
            "session_id",
            "event",
            "payload_json",
            "recorded_at",
        }
    ),
}
REQUIRED_AGENT_STATE_TABLES = frozenset(REQUIRED_AGENT_STATE_COLUMNS)

_CREATE_SCHEMA_SQL = f"CREATE SCHEMA IF NOT EXISTS {AGENT_STATE_SCHEMA}"
_CREATE_MIGRATION_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {AGENT_STATE_MIGRATION_TABLE} (
    version INTEGER PRIMARY KEY CHECK (version >= 1),
    description TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""
_MIGRATION_1_STATEMENTS = (
    f"""
    CREATE TABLE {AGENT_STATE_SCHEMA}.agent_sessions (
        session_id TEXT PRIMARY KEY
            CHECK (char_length(session_id) BETWEEN 1 AND 64),
        owner_subject TEXT,
        owner_identity_source TEXT,
        turn_number INTEGER NOT NULL DEFAULT 0 CHECK (turn_number >= 0),
        phase TEXT NOT NULL CHECK (
            phase IN (
                'idle',
                'collecting_information',
                'awaiting_confirmation',
                'confirmed',
                'submitted',
                'cancelled'
            )
        ),
        active_draft_id TEXT,
        draft_revision INTEGER CHECK (draft_revision IS NULL OR draft_revision >= 1),
        pending_confirmation BOOLEAN NOT NULL DEFAULT FALSE,
        checkpoint_backend TEXT NOT NULL,
        state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
        deleted_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (
            (owner_subject IS NULL AND owner_identity_source IS NULL)
            OR
            (owner_subject IS NOT NULL AND owner_identity_source IS NOT NULL)
        )
    )
    """,
    f"""
    CREATE TABLE {AGENT_STATE_SCHEMA}.application_draft_snapshots (
        draft_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        session_id TEXT NOT NULL,
        status TEXT NOT NULL,
        payload_json JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (draft_id, revision),
        FOREIGN KEY (session_id)
            REFERENCES {AGENT_STATE_SCHEMA}.agent_sessions(session_id)
            ON DELETE CASCADE
    )
    """,
    f"""
    CREATE TABLE {AGENT_STATE_SCHEMA}.conversation_messages (
        message_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        turn_number INTEGER NOT NULL CHECK (turn_number >= 1),
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        content TEXT NOT NULL,
        redacted BOOLEAN NOT NULL DEFAULT FALSE,
        truncated BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (session_id, turn_number, role),
        FOREIGN KEY (session_id)
            REFERENCES {AGENT_STATE_SCHEMA}.agent_sessions(session_id)
            ON DELETE CASCADE
    )
    """,
    f"""
    CREATE TABLE {AGENT_STATE_SCHEMA}.approval_submissions (
        idempotency_key TEXT PRIMARY KEY,
        submission_id TEXT NOT NULL UNIQUE,
        draft_id TEXT NOT NULL UNIQUE,
        session_id TEXT NOT NULL,
        employee_id TEXT NOT NULL,
        payload_json JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    f"""
    CREATE TABLE {AGENT_STATE_SCHEMA}.submission_audit_records (
        audit_id TEXT PRIMARY KEY,
        submission_id TEXT NOT NULL,
        draft_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        event TEXT NOT NULL CHECK (event IN ('submitted', 'idempotent_replay')),
        payload_json JSONB NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (submission_id)
            REFERENCES {AGENT_STATE_SCHEMA}.approval_submissions(submission_id)
            ON DELETE RESTRICT
    )
    """,
    f"""
    CREATE INDEX idx_agent_runtime_sessions_updated
    ON {AGENT_STATE_SCHEMA}.agent_sessions (updated_at DESC)
    WHERE deleted_at IS NULL
    """,
    f"""
    CREATE INDEX idx_agent_runtime_drafts_session
    ON {AGENT_STATE_SCHEMA}.application_draft_snapshots (session_id, updated_at DESC)
    """,
    f"""
    CREATE INDEX idx_agent_runtime_messages_session
    ON {AGENT_STATE_SCHEMA}.conversation_messages (session_id, turn_number DESC)
    """,
    f"""
    CREATE INDEX idx_agent_runtime_submissions_session
    ON {AGENT_STATE_SCHEMA}.approval_submissions (session_id, created_at DESC)
    """,
    f"""
    CREATE INDEX idx_agent_runtime_audits_draft
    ON {AGENT_STATE_SCHEMA}.submission_audit_records (draft_id, recorded_at)
    """,
)


class _CursorLike(Protocol):
    def fetchone(self) -> Sequence[Any] | None:
        """Return one result row."""

    def fetchall(self) -> Sequence[Sequence[Any]]:
        """Return all result rows."""


class PostgresStateSchemaConnection(Protocol):
    def execute(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> _CursorLike:
        """Execute one PostgreSQL schema statement."""


class PostgresStateSchemaError(RuntimeError):
    """Raised when the Agent runtime schema is newer, incomplete, or drifted."""


@dataclass(frozen=True, slots=True)
class PostgresStateSchemaStatus:
    schema_name: str
    supported_version: int
    current_version: int
    tables: tuple[str, ...]
    missing_tables: tuple[str, ...]
    missing_columns: dict[str, tuple[str, ...]]

    @property
    def initialized(self) -> bool:
        return self.current_version > 0

    @property
    def ready(self) -> bool:
        return (
            self.current_version == self.supported_version
            and not self.missing_tables
            and not self.missing_columns
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_name": self.schema_name,
            "supported_version": self.supported_version,
            "current_version": self.current_version,
            "initialized": self.initialized,
            "ready": self.ready,
            "tables": list(self.tables),
            "missing_tables": list(self.missing_tables),
            "missing_columns": {
                table: list(columns) for table, columns in self.missing_columns.items()
            },
        }


def inspect_postgres_state_schema(
    connection: PostgresStateSchemaConnection,
) -> PostgresStateSchemaStatus:
    """Inspect the checked-in Agent runtime schema without mutating PostgreSQL."""

    migration_relation = connection.execute(
        "SELECT to_regclass(%s)",
        (AGENT_STATE_MIGRATION_TABLE,),
    ).fetchone()
    if migration_relation is None or migration_relation[0] is None:
        return PostgresStateSchemaStatus(
            schema_name=AGENT_STATE_SCHEMA,
            supported_version=AGENT_STATE_SCHEMA_VERSION,
            current_version=0,
            tables=(),
            missing_tables=tuple(sorted(REQUIRED_AGENT_STATE_TABLES)),
            missing_columns={},
        )

    version_row = connection.execute(
        f"SELECT COALESCE(MAX(version), 0) FROM {AGENT_STATE_MIGRATION_TABLE}"
    ).fetchone()
    current_version = int(version_row[0]) if version_row is not None else 0
    table_rows = connection.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """,
        (AGENT_STATE_SCHEMA,),
    ).fetchall()
    tables = tuple(str(row[0]) for row in table_rows)
    column_rows = connection.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = %s
        ORDER BY table_name, ordinal_position
        """,
        (AGENT_STATE_SCHEMA,),
    ).fetchall()
    actual_columns: dict[str, set[str]] = {}
    for table_name, column_name in column_rows:
        actual_columns.setdefault(str(table_name), set()).add(str(column_name))

    missing_tables = tuple(sorted(REQUIRED_AGENT_STATE_TABLES.difference(tables)))
    missing_columns = {
        table: tuple(sorted(required.difference(actual_columns.get(table, set()))))
        for table, required in REQUIRED_AGENT_STATE_COLUMNS.items()
        if table in tables and required.difference(actual_columns.get(table, set()))
    }
    return PostgresStateSchemaStatus(
        schema_name=AGENT_STATE_SCHEMA,
        supported_version=AGENT_STATE_SCHEMA_VERSION,
        current_version=current_version,
        tables=tables,
        missing_tables=missing_tables,
        missing_columns=missing_columns,
    )


def initialize_postgres_state_schema(
    connection: PostgresStateSchemaConnection,
) -> PostgresStateSchemaStatus:
    """Apply the versioned Phase 38 runtime schema inside the caller transaction."""

    connection.execute(_CREATE_SCHEMA_SQL)
    connection.execute(_CREATE_MIGRATION_TABLE_SQL)
    connection.execute(f"LOCK TABLE {AGENT_STATE_MIGRATION_TABLE} IN EXCLUSIVE MODE")
    version_row = connection.execute(
        f"SELECT COALESCE(MAX(version), 0) FROM {AGENT_STATE_MIGRATION_TABLE}"
    ).fetchone()
    current_version = int(version_row[0]) if version_row is not None else 0
    if current_version > AGENT_STATE_SCHEMA_VERSION:
        raise PostgresStateSchemaError(
            "PostgreSQL Agent state schema is newer than this application supports: "
            f"{current_version} > {AGENT_STATE_SCHEMA_VERSION}"
        )

    if current_version < 1:
        for statement in _MIGRATION_1_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            f"""
            INSERT INTO {AGENT_STATE_MIGRATION_TABLE} (version, description)
            VALUES (%s, %s)
            """,
            (1, "create shared Agent runtime state tables"),
        )

    status = inspect_postgres_state_schema(connection)
    if not status.ready:
        raise PostgresStateSchemaError(
            "PostgreSQL Agent state schema failed validation after setup: "
            f"version={status.current_version}, missing_tables={status.missing_tables}, "
            f"missing_columns={status.missing_columns}"
        )
    return status


class PostgresAgentStateSchemaManager:
    """Open short-lived Psycopg connections for explicit schema administration."""

    def __init__(self, dsn: str, *, connect_timeout_seconds: float = 5.0) -> None:
        normalized_dsn = dsn.strip()
        if not normalized_dsn:
            raise ValueError("dsn must not be blank")
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be greater than zero")
        self._dsn = normalized_dsn
        self._connect_timeout_seconds = connect_timeout_seconds

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        connect_timeout_seconds: float = 5.0,
    ) -> Self:
        return cls(dsn, connect_timeout_seconds=connect_timeout_seconds)

    def _connect(self):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - broken installation only
            raise RuntimeError("PostgreSQL Agent state schema requires psycopg") from exc
        return psycopg.connect(
            self._dsn,
            connect_timeout=max(1, ceil(self._connect_timeout_seconds)),
        )

    def setup(self) -> PostgresStateSchemaStatus:
        with self._connect() as connection:
            return initialize_postgres_state_schema(connection)

    def status(self) -> PostgresStateSchemaStatus:
        with self._connect() as connection:
            return inspect_postgres_state_schema(connection)


__all__ = [
    "AGENT_STATE_MIGRATION_TABLE",
    "AGENT_STATE_SCHEMA",
    "AGENT_STATE_SCHEMA_VERSION",
    "PostgresAgentStateSchemaManager",
    "PostgresStateSchemaConnection",
    "PostgresStateSchemaError",
    "PostgresStateSchemaStatus",
    "REQUIRED_AGENT_STATE_COLUMNS",
    "REQUIRED_AGENT_STATE_TABLES",
    "initialize_postgres_state_schema",
    "inspect_postgres_state_schema",
]
