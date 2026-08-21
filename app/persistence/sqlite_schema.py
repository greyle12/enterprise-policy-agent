from __future__ import annotations

import sqlite3
from pathlib import Path

SQLITE_SCHEMA_VERSION = 2


def normalize_database_path(database_path: str | Path) -> Path:
    """Return a concrete on-disk SQLite path suitable for restart recovery."""

    normalized = Path(database_path).expanduser()
    if str(normalized).strip() in {"", ":memory:"}:
        raise ValueError("database_path must reference an on-disk SQLite database")
    if normalized.exists() and normalized.is_dir():
        raise ValueError("database_path must not reference a directory")
    return normalized


def connect_database(database_path: str | Path) -> sqlite3.Connection:
    """Open one configured SQLite connection with safe local defaults."""

    path = normalize_database_path(database_path)
    connection = sqlite3.connect(
        path,
        timeout=5.0,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_database(database_path: str | Path) -> Path:
    """Create or migrate the runtime SQLite schema idempotently."""

    path = normalize_database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect_database(path)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("BEGIN IMMEDIATE")
        current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current_version > SQLITE_SCHEMA_VERSION:
            raise RuntimeError(
                "SQLite schema version is newer than this application supports: "
                f"{current_version} > {SQLITE_SCHEMA_VERSION}"
            )

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS langgraph_checkpoints (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                parent_checkpoint_id TEXT,
                checkpoint_type TEXT NOT NULL,
                checkpoint BLOB NOT NULL,
                metadata_type TEXT NOT NULL,
                metadata BLOB NOT NULL,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
            );

            CREATE INDEX IF NOT EXISTS idx_langgraph_checkpoint_latest
                ON langgraph_checkpoints (
                    thread_id,
                    checkpoint_ns,
                    checkpoint_id DESC
                );

            CREATE TABLE IF NOT EXISTS langgraph_blobs (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                channel TEXT NOT NULL,
                version TEXT NOT NULL,
                value_type TEXT NOT NULL,
                value BLOB NOT NULL,
                PRIMARY KEY (
                    thread_id,
                    checkpoint_ns,
                    channel,
                    version
                )
            );

            CREATE TABLE IF NOT EXISTS langgraph_writes (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                write_index INTEGER NOT NULL,
                channel TEXT NOT NULL,
                value_type TEXT NOT NULL,
                value BLOB NOT NULL,
                task_path TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (
                    thread_id,
                    checkpoint_ns,
                    checkpoint_id,
                    task_id,
                    write_index
                )
            );

            CREATE TABLE IF NOT EXISTS agent_sessions (
                session_id TEXT PRIMARY KEY,
                turn_number INTEGER NOT NULL,
                phase TEXT NOT NULL,
                active_draft_id TEXT,
                draft_revision INTEGER,
                pending_confirmation INTEGER NOT NULL,
                checkpoint_backend TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversation_messages (
                message_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                turn_number INTEGER NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                redacted INTEGER NOT NULL DEFAULT 0,
                truncated INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE (session_id, turn_number, role)
            );

            CREATE INDEX IF NOT EXISTS idx_conversation_messages_session
                ON conversation_messages (
                    session_id,
                    turn_number DESC
                );

            CREATE TABLE IF NOT EXISTS application_draft_snapshots (
                draft_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json BLOB NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (draft_id, revision),
                FOREIGN KEY (session_id)
                    REFERENCES agent_sessions(session_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_draft_snapshots_session
                ON application_draft_snapshots (
                    session_id,
                    updated_at DESC
                );

            CREATE TABLE IF NOT EXISTS approval_submissions (
                idempotency_key TEXT PRIMARY KEY,
                submission_id TEXT NOT NULL UNIQUE,
                draft_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL,
                employee_id TEXT NOT NULL,
                payload_json BLOB NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_submissions_session
                ON approval_submissions (session_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS submission_audit_records (
                audit_id TEXT PRIMARY KEY,
                submission_id TEXT NOT NULL,
                draft_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                event TEXT NOT NULL,
                payload_json BLOB NOT NULL,
                recorded_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_submission_audits_draft
                ON submission_audit_records (
                    draft_id,
                    recorded_at
                );
            """
        )
        connection.execute(f"PRAGMA user_version = {SQLITE_SCHEMA_VERSION}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return path
