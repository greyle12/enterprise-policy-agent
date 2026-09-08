from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.memory import ConversationRole
from app.persistence.postgres_memory import (
    ConversationSessionUnavailableError,
    ConversationTurnConflictError,
    PostgresConversationMemoryStore,
)

_NOW = datetime(2026, 8, 28, 13, 0, tzinfo=UTC)
_SESSION_ID = "postgres-conversation"


class _Cursor:
    def __init__(self, *, one=None, rows=()) -> None:
        self._one = one
        self._rows = tuple(rows)

    async def fetchone(self):
        return self._one

    async def fetchall(self):
        return self._rows


@dataclass(frozen=True, slots=True)
class _ExpectedExecution:
    contains: str
    one: object = None
    rows: tuple[Sequence[object], ...] = ()


class _Connection:
    def __init__(self, executions: Sequence[_ExpectedExecution]) -> None:
        self.remaining = list(executions)
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, params=None) -> _Cursor:
        if not self.remaining:
            raise AssertionError(f"unexpected SQL statement: {' '.join(query.split())}")
        expected = self.remaining.pop(0)
        normalized = " ".join(query.split())
        assert expected.contains in normalized
        values = tuple(params or ())
        self.executed.append((normalized, values))
        return _Cursor(one=expected.one, rows=expected.rows)


class _Pool:
    def __init__(self, executions: Sequence[_ExpectedExecution]) -> None:
        self.connection_instance = _Connection(executions)
        self.commits = 0
        self.rollbacks = 0

    @asynccontextmanager
    async def connection(self, timeout: float | None = None) -> AsyncIterator[_Connection]:
        del timeout
        try:
            yield self.connection_instance
        except BaseException:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def assert_consumed(self) -> None:
        assert not self.connection_instance.remaining


@pytest.mark.asyncio
async def test_appends_sanitized_pair_and_prunes_complete_old_turns_atomically() -> None:
    pool = _Pool(
        (
            _ExpectedExecution(
                "FROM agent_runtime.agent_sessions WHERE session_id = %s FOR UPDATE",
                one=(None,),
            ),
            _ExpectedExecution(
                "SELECT COALESCE(MAX(turn_number), 0) + 1",
                one=(3,),
            ),
            _ExpectedExecution(
                "INSERT INTO agent_runtime.conversation_messages",
                rows=(("user-id",), ("assistant-id",)),
            ),
            _ExpectedExecution("DELETE FROM agent_runtime.conversation_messages"),
        )
    )
    store = PostgresConversationMemoryStore(
        pool=pool,
        max_stored_turns=2,
        clock=lambda: _NOW,
    )

    user, assistant = await store.append_turn(
        _SESSION_ID,
        user_message=" password=hunter2 ",
        assistant_message="不要发送 token=abcdefghijk",
    )

    assert user.turn_number == assistant.turn_number == 3
    assert user.role is ConversationRole.USER
    assert assistant.role is ConversationRole.ASSISTANT
    assert user.created_at == assistant.created_at == _NOW
    assert user.redacted is True
    assert assistant.redacted is True
    assert "hunter2" not in user.content
    assert "abcdefghijk" not in assistant.content
    insert_sql, insert_params = pool.connection_instance.executed[2]
    assert "ON CONFLICT DO NOTHING" in insert_sql
    assert "RETURNING message_id" in insert_sql
    assert len(insert_params) == 16
    _, prune_params = pool.connection_instance.executed[3]
    assert prune_params == (_SESSION_ID, 2)
    assert pool.commits == 1
    assert pool.rollbacks == 0
    pool.assert_consumed()


@pytest.mark.asyncio
@pytest.mark.parametrize("session_row", [None, (_NOW,)])
async def test_append_fails_closed_for_missing_or_tombstoned_session(session_row) -> None:
    pool = _Pool(
        (
            _ExpectedExecution(
                "FROM agent_runtime.agent_sessions WHERE session_id = %s FOR UPDATE",
                one=session_row,
            ),
        )
    )

    with pytest.raises(ConversationSessionUnavailableError):
        await PostgresConversationMemoryStore(pool=pool).append_turn(
            _SESSION_ID,
            user_message="问题",
            assistant_message="回答",
        )

    assert pool.commits == 0
    assert pool.rollbacks == 1
    pool.assert_consumed()


@pytest.mark.asyncio
async def test_incomplete_pair_insert_rolls_back_as_turn_conflict() -> None:
    pool = _Pool(
        (
            _ExpectedExecution("FROM agent_runtime.agent_sessions", one=(None,)),
            _ExpectedExecution("SELECT COALESCE(MAX(turn_number), 0) + 1", one=(1,)),
            _ExpectedExecution(
                "INSERT INTO agent_runtime.conversation_messages",
                rows=(("only-one-row",),),
            ),
        )
    )

    with pytest.raises(ConversationTurnConflictError, match="incomplete write"):
        await PostgresConversationMemoryStore(pool=pool).append_turn(
            _SESSION_ID,
            user_message="问题",
            assistant_message="回答",
        )

    assert pool.rollbacks == 1
    pool.assert_consumed()


@pytest.mark.asyncio
async def test_snapshot_returns_recent_messages_in_chronological_role_order() -> None:
    rows = (
        ("u2", _SESSION_ID, 2, "user", "用户 2", _NOW, False, False, 6),
        ("a2", _SESSION_ID, 2, "assistant", "助手 2", _NOW, False, False, 6),
        ("u3", _SESSION_ID, 3, "user", "用户 3", _NOW, True, False, 6),
        ("a3", _SESSION_ID, 3, "assistant", "助手 3", _NOW, False, True, 6),
    )
    pool = _Pool(
        (
            _ExpectedExecution(
                "WITH ranked_messages AS",
                rows=rows,
            ),
        )
    )

    snapshot = await PostgresConversationMemoryStore(pool=pool).get_snapshot(
        _SESSION_ID,
        limit=4,
    )

    assert snapshot.total_message_count == 6
    assert snapshot.backend == "postgresql"
    assert snapshot.survives_process_restart is True
    assert [(message.turn_number, message.role) for message in snapshot.messages] == [
        (2, ConversationRole.USER),
        (2, ConversationRole.ASSISTANT),
        (3, ConversationRole.USER),
        (3, ConversationRole.ASSISTANT),
    ]
    assert snapshot.messages[2].redacted is True
    assert snapshot.messages[3].truncated is True
    query, params = pool.connection_instance.executed[0]
    assert "session.deleted_at IS NULL" in query
    assert "ORDER BY recency_rank DESC" in query
    assert params == (_SESSION_ID, 4)
    pool.assert_consumed()


@pytest.mark.asyncio
async def test_snapshot_for_unknown_or_tombstoned_session_is_empty() -> None:
    pool = _Pool((_ExpectedExecution("WITH ranked_messages AS", rows=()),))

    snapshot = await PostgresConversationMemoryStore(pool=pool).get_snapshot(
        _SESSION_ID,
        limit=20,
    )

    assert snapshot.messages == ()
    assert snapshot.total_message_count == 0
    pool.assert_consumed()


@pytest.mark.asyncio
async def test_clear_session_locks_session_then_deletes_only_its_messages() -> None:
    pool = _Pool(
        (
            _ExpectedExecution(
                "FROM agent_runtime.agent_sessions WHERE session_id = %s FOR UPDATE",
                one=(_NOW,),
            ),
            _ExpectedExecution("DELETE FROM agent_runtime.conversation_messages"),
        )
    )

    await PostgresConversationMemoryStore(pool=pool).clear_session(_SESSION_ID)

    _, delete_params = pool.connection_instance.executed[1]
    assert delete_params == (_SESSION_ID,)
    assert pool.commits == 1
    pool.assert_consumed()


@pytest.mark.asyncio
async def test_validates_limits_and_blank_content_before_database_access() -> None:
    with pytest.raises(ValueError, match="positive"):
        PostgresConversationMemoryStore(pool=_Pool(()), max_stored_turns=0)

    pool = _Pool(())
    store = PostgresConversationMemoryStore(pool=pool)
    with pytest.raises(ValueError, match="between 1 and 100"):
        await store.get_snapshot(_SESSION_ID, limit=0)
    with pytest.raises(ValueError, match="must not be blank"):
        await store.append_turn(
            _SESSION_ID,
            user_message=" ",
            assistant_message="回答",
        )

    assert pool.commits == 0
    assert pool.rollbacks == 0


def test_postgres_conversation_store_reports_shared_durable_backend() -> None:
    assert PostgresConversationMemoryStore.backend_name == "postgresql"
    assert PostgresConversationMemoryStore.survives_process_restart is True
