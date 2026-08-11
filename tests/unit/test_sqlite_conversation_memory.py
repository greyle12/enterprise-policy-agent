from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.memory import ConversationRole
from app.persistence import SQLiteConversationMemoryStore
from app.persistence.sqlite_schema import SQLITE_SCHEMA_VERSION

_NOW = datetime(2026, 8, 11, 11, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_sqlite_memory_survives_store_recreation(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.db"
    first_store = SQLiteConversationMemoryStore(
        database_path,
        clock=lambda: _NOW,
    )
    await first_store.append_turn(
        "sqlite-memory",
        user_message="差旅住宿标准是多少？",
        assistant_message="请按照制度标准执行。",
    )

    restored = await SQLiteConversationMemoryStore(database_path).get_snapshot(
        "sqlite-memory", limit=20
    )

    assert restored.backend == "sqlite"
    assert restored.survives_process_restart is True
    assert restored.total_message_count == 2
    assert [message.role for message in restored.messages] == [
        ConversationRole.USER,
        ConversationRole.ASSISTANT,
    ]
    assert restored.messages[0].created_at == _NOW


@pytest.mark.asyncio
async def test_sqlite_memory_redacts_secrets_before_write(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "redaction.db"
    store = SQLiteConversationMemoryStore(database_path)

    await store.append_turn(
        "sqlite-redaction",
        user_message="password=hunter2，token=abcdefghijk",
        assistant_message="不要在消息中发送密钥。",
    )

    snapshot = await store.get_snapshot("sqlite-redaction", limit=20)
    user_message = snapshot.messages[0]
    assert user_message.redacted is True
    assert "hunter2" not in user_message.content
    assert "abcdefghijk" not in user_message.content
    with sqlite3.connect(database_path) as connection:
        raw = connection.execute(
            "SELECT content FROM conversation_messages WHERE role = 'user'"
        ).fetchone()[0]
    assert "hunter2" not in raw
    assert "abcdefghijk" not in raw


@pytest.mark.asyncio
async def test_sqlite_memory_prunes_by_complete_turn(tmp_path: Path) -> None:
    store = SQLiteConversationMemoryStore(
        tmp_path / "retention.db",
        max_stored_turns=2,
    )
    for number in range(1, 4):
        await store.append_turn(
            "sqlite-retention",
            user_message=f"用户 {number}",
            assistant_message=f"助手 {number}",
        )

    snapshot = await store.get_snapshot("sqlite-retention", limit=100)

    assert snapshot.total_message_count == 4
    assert [message.turn_number for message in snapshot.messages] == [
        2,
        2,
        3,
        3,
    ]


@pytest.mark.asyncio
async def test_sqlite_memory_allocates_turns_concurrently(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "concurrent-memory.db"
    first = SQLiteConversationMemoryStore(database_path)
    second = SQLiteConversationMemoryStore(database_path)

    await asyncio.gather(
        first.append_turn(
            "sqlite-concurrent",
            user_message="问题 A",
            assistant_message="回答 A",
        ),
        second.append_turn(
            "sqlite-concurrent",
            user_message="问题 B",
            assistant_message="回答 B",
        ),
    )

    snapshot = await first.get_snapshot("sqlite-concurrent", limit=20)
    assert snapshot.total_message_count == 4
    assert sorted({message.turn_number for message in snapshot.messages}) == [
        1,
        2,
    ]


@pytest.mark.asyncio
async def test_sqlite_memory_clear_is_session_scoped(tmp_path: Path) -> None:
    store = SQLiteConversationMemoryStore(tmp_path / "clear-memory.db")
    for session_id in ("sqlite-clear-a", "sqlite-clear-b"):
        await store.append_turn(
            session_id,
            user_message="问题",
            assistant_message="回答",
        )

    await store.clear_session("sqlite-clear-a")

    first = await store.get_snapshot("sqlite-clear-a", limit=20)
    second = await store.get_snapshot("sqlite-clear-b", limit=20)
    assert first.total_message_count == 0
    assert second.total_message_count == 2


def test_schema_migrates_existing_version_one_database(tmp_path: Path) -> None:
    database_path = tmp_path / "version-one.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE legacy_marker (id INTEGER)")
        connection.execute("PRAGMA user_version = 1")

    SQLiteConversationMemoryStore(database_path)

    with sqlite3.connect(database_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        table = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'conversation_messages'
            """
        ).fetchone()
    assert version == SQLITE_SCHEMA_VERSION
    assert table is not None
