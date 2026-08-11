from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.memory.conversation import (
    DEFAULT_STORED_TURN_LIMIT,
    MAX_HISTORY_MESSAGE_LIMIT,
    ConversationMemorySnapshot,
    ConversationMessage,
    ConversationRole,
    build_memory_message_id,
    sanitize_memory_content,
)
from app.persistence.sqlite_schema import (
    connect_database,
    initialize_database,
)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SQLiteConversationMemoryStore:
    """Persist bounded user/assistant turns in the shared runtime database."""

    backend_name = "sqlite"
    survives_process_restart = True

    def __init__(
        self,
        database_path: str | Path,
        *,
        max_stored_turns: int = DEFAULT_STORED_TURN_LIMIT,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_stored_turns < 1:
            raise ValueError("max_stored_turns must be positive")
        self.database_path = initialize_database(database_path)
        self.max_stored_turns = max_stored_turns
        self._clock = clock or (lambda: datetime.now(UTC))

    async def append_turn(
        self,
        session_id: str,
        *,
        user_message: str,
        assistant_message: str,
    ) -> tuple[ConversationMessage, ConversationMessage]:
        user_content, user_redacted, user_truncated = sanitize_memory_content(user_message)
        assistant_content, assistant_redacted, assistant_truncated = sanitize_memory_content(
            assistant_message
        )
        return await asyncio.to_thread(
            self._append_turn,
            session_id,
            user_content,
            user_redacted,
            user_truncated,
            assistant_content,
            assistant_redacted,
            assistant_truncated,
            _aware_utc(self._clock()),
        )

    def _append_turn(
        self,
        session_id: str,
        user_content: str,
        user_redacted: bool,
        user_truncated: bool,
        assistant_content: str,
        assistant_redacted: bool,
        assistant_truncated: bool,
        created_at: datetime,
    ) -> tuple[ConversationMessage, ConversationMessage]:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT COALESCE(MAX(turn_number), 0) + 1
                FROM conversation_messages
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("could not allocate memory turn number")
            turn_number = int(row[0])
            user = ConversationMessage(
                message_id=build_memory_message_id(
                    session_id,
                    turn_number,
                    ConversationRole.USER,
                ),
                session_id=session_id,
                turn_number=turn_number,
                role=ConversationRole.USER,
                content=user_content,
                created_at=created_at,
                redacted=user_redacted,
                truncated=user_truncated,
            )
            assistant = ConversationMessage(
                message_id=build_memory_message_id(
                    session_id,
                    turn_number,
                    ConversationRole.ASSISTANT,
                ),
                session_id=session_id,
                turn_number=turn_number,
                role=ConversationRole.ASSISTANT,
                content=assistant_content,
                created_at=created_at,
                redacted=assistant_redacted,
                truncated=assistant_truncated,
            )
            connection.executemany(
                """
                INSERT INTO conversation_messages (
                    message_id,
                    session_id,
                    turn_number,
                    role,
                    content,
                    redacted,
                    truncated,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        message.message_id,
                        message.session_id,
                        message.turn_number,
                        message.role.value,
                        message.content,
                        int(message.redacted),
                        int(message.truncated),
                        message.created_at.isoformat(),
                    )
                    for message in (user, assistant)
                ),
            )
            oldest_retained_turn = turn_number - self.max_stored_turns + 1
            if oldest_retained_turn > 1:
                connection.execute(
                    """
                    DELETE FROM conversation_messages
                    WHERE session_id = ? AND turn_number < ?
                    """,
                    (session_id, oldest_retained_turn),
                )
            connection.commit()
            return user, assistant
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def get_snapshot(
        self,
        session_id: str,
        *,
        limit: int,
    ) -> ConversationMemorySnapshot:
        if not 1 <= limit <= MAX_HISTORY_MESSAGE_LIMIT:
            raise ValueError("limit must be between 1 and 100")
        return await asyncio.to_thread(
            self._get_snapshot,
            session_id,
            limit,
        )

    def _get_snapshot(
        self,
        session_id: str,
        limit: int,
    ) -> ConversationMemorySnapshot:
        connection = connect_database(self.database_path)
        try:
            count_row = connection.execute(
                """
                SELECT COUNT(*)
                FROM conversation_messages
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            total = int(count_row[0]) if count_row is not None else 0
            rows = connection.execute(
                """
                SELECT *
                FROM conversation_messages
                WHERE session_id = ?
                ORDER BY
                    turn_number DESC,
                    CASE role WHEN 'assistant' THEN 1 ELSE 0 END DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
            messages = tuple(
                ConversationMessage(
                    message_id=row["message_id"],
                    session_id=row["session_id"],
                    turn_number=int(row["turn_number"]),
                    role=ConversationRole(row["role"]),
                    content=row["content"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    redacted=bool(row["redacted"]),
                    truncated=bool(row["truncated"]),
                )
                for row in reversed(rows)
            )
            return ConversationMemorySnapshot(
                session_id=session_id,
                messages=messages,
                total_message_count=total,
                backend=self.backend_name,
                survives_process_restart=self.survives_process_restart,
            )
        finally:
            connection.close()

    async def clear_session(self, session_id: str) -> None:
        await asyncio.to_thread(self._clear_session, session_id)

    def _clear_session(self, session_id: str) -> None:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM conversation_messages WHERE session_id = ?",
                (session_id,),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
