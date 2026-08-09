from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.persistence.sqlite_checkpointer import SQLiteCheckpointSaver
from app.persistence.sqlite_schema import (
    SQLITE_SCHEMA_VERSION,
    initialize_database,
)


def _checkpoint(checkpoint_id: str):
    return {
        "v": 2,
        "id": checkpoint_id,
        "ts": "2026-08-08T10:00:00+00:00",
        "channel_values": {"value": {"count": 1}},
        "channel_versions": {"value": "00000000000000000000000000000001.1"},
        "versions_seen": {},
        "updated_channels": ["value"],
    }


def test_initializes_schema_idempotently(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime" / "agent.db"

    first = initialize_database(database_path)
    second = initialize_database(database_path)

    assert first == second == database_path
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0] == SQLITE_SCHEMA_VERSION
        tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            )
        }
    assert {
        "langgraph_checkpoints",
        "langgraph_blobs",
        "langgraph_writes",
        "agent_sessions",
        "application_draft_snapshots",
        "approval_submissions",
        "submission_audit_records",
    } <= tables


def test_rejects_database_from_newer_schema_version(
    tmp_path: Path,
) -> None:
    database_path = initialize_database(tmp_path / "future.db")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            f"PRAGMA user_version = {SQLITE_SCHEMA_VERSION + 1}"
        )

    with pytest.raises(RuntimeError, match="newer"):
        initialize_database(database_path)


def test_rejects_memory_and_directory_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="on-disk"):
        SQLiteCheckpointSaver(":memory:")
    with pytest.raises(ValueError, match="directory"):
        SQLiteCheckpointSaver(tmp_path)


def test_round_trips_checkpoint_writes_and_delete(
    tmp_path: Path,
) -> None:
    saver = SQLiteCheckpointSaver(tmp_path / "checkpoint.db")
    config = {
        "configurable": {
            "thread_id": "sqlite-checkpoint-roundtrip",
            "checkpoint_ns": "",
        }
    }
    checkpoint = _checkpoint(
        "00000000-0000-6000-8000-000000000001"
    )

    stored_config = saver.put(
        config,
        checkpoint,
        {"source": "loop", "step": 1},
        checkpoint["channel_versions"],
    )
    saver.put_writes(
        stored_config,
        [("result", {"ok": True})],
        "task-001",
    )

    restored = saver.get_tuple(
        {
            "configurable": {
                "thread_id": "sqlite-checkpoint-roundtrip"
            }
        }
    )
    assert restored is not None
    assert restored.checkpoint["channel_values"] == {
        "value": {"count": 1}
    }
    assert restored.metadata["step"] == 1
    assert restored.pending_writes == [
        ("task-001", "result", {"ok": True})
    ]
    assert len(list(saver.list(config))) == 1

    saver.delete_thread("sqlite-checkpoint-roundtrip")

    assert saver.get_tuple(config) is None
