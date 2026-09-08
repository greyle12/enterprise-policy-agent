from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from app.memory.conversation import (
    DEFAULT_STORED_TURN_LIMIT,
    MAX_HISTORY_MESSAGE_LIMIT,
    ConversationMemorySnapshot,
    ConversationMessage,
    ConversationRole,
    build_memory_message_id,
    sanitize_memory_content,
)
from app.persistence.postgres_connection import (
    PostgresStateConnection,
    PostgresStateConnectionPool,
)
from app.persistence.postgres_schema import AGENT_STATE_SCHEMA


class PostgresConversationMemoryError(RuntimeError):
    """Base error for PostgreSQL conversation persistence failures."""


class ConversationSessionUnavailableError(PostgresConversationMemoryError):
    """Raised when conversation state cannot be appended to a live session."""


class ConversationTurnConflictError(PostgresConversationMemoryError):
    """Raised when a conversation turn loses its uniqueness guard."""


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _fetchone(
    connection: PostgresStateConnection,
    query: str,
    params: Sequence[Any] | None = None,
) -> Sequence[Any] | None:
    cursor = await connection.execute(query, params)
    return await cursor.fetchone()


async def _fetchall(
    connection: PostgresStateConnection,
    query: str,
    params: Sequence[Any] | None = None,
) -> Sequence[Sequence[Any]]:
    cursor = await connection.execute(query, params)
    return await cursor.fetchall()


class PostgresConversationMemoryStore:
    """Persist bounded sanitized conversation turns in PostgreSQL."""

    backend_name = "postgresql"
    survives_process_restart = True

    def __init__(
        self,
        *,
        pool: PostgresStateConnectionPool,
        max_stored_turns: int = DEFAULT_STORED_TURN_LIMIT,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_stored_turns < 1:
            raise ValueError("max_stored_turns must be positive")
        self._pool = pool
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
        created_at = _aware_utc(self._clock())

        async with self._pool.connection() as connection:
            session = await _fetchone(
                connection,
                f"""
                SELECT deleted_at
                FROM {AGENT_STATE_SCHEMA}.agent_sessions
                WHERE session_id = %s
                FOR UPDATE
                """,
                (session_id,),
            )
            if session is None:
                raise ConversationSessionUnavailableError(
                    "conversation turn requires an existing session"
                )
            if session[0] is not None:
                raise ConversationSessionUnavailableError(
                    "conversation turn cannot be appended to a tombstoned session"
                )

            turn_row = await _fetchone(
                connection,
                f"""
                SELECT COALESCE(MAX(turn_number), 0) + 1
                FROM {AGENT_STATE_SCHEMA}.conversation_messages
                WHERE session_id = %s
                """,
                (session_id,),
            )
            if turn_row is None:
                raise PostgresConversationMemoryError("could not allocate conversation turn number")
            turn_number = int(turn_row[0])
            user = self._message(
                session_id=session_id,
                turn_number=turn_number,
                role=ConversationRole.USER,
                content=user_content,
                created_at=created_at,
                redacted=user_redacted,
                truncated=user_truncated,
            )
            assistant = self._message(
                session_id=session_id,
                turn_number=turn_number,
                role=ConversationRole.ASSISTANT,
                content=assistant_content,
                created_at=created_at,
                redacted=assistant_redacted,
                truncated=assistant_truncated,
            )
            inserted = await _fetchall(
                connection,
                f"""
                INSERT INTO {AGENT_STATE_SCHEMA}.conversation_messages (
                    message_id,
                    session_id,
                    turn_number,
                    role,
                    content,
                    redacted,
                    truncated,
                    created_at
                ) VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s),
                    (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING message_id
                """,
                (*self._message_params(user), *self._message_params(assistant)),
            )
            if len(inserted) != 2:
                raise ConversationTurnConflictError(
                    "conversation turn uniqueness guard rejected an incomplete write"
                )

            oldest_retained_turn = turn_number - self.max_stored_turns + 1
            if oldest_retained_turn > 1:
                await connection.execute(
                    f"""
                    DELETE FROM {AGENT_STATE_SCHEMA}.conversation_messages
                    WHERE session_id = %s AND turn_number < %s
                    """,
                    (session_id, oldest_retained_turn),
                )
        return user, assistant

    @staticmethod
    def _message(
        *,
        session_id: str,
        turn_number: int,
        role: ConversationRole,
        content: str,
        created_at: datetime,
        redacted: bool,
        truncated: bool,
    ) -> ConversationMessage:
        return ConversationMessage(
            message_id=build_memory_message_id(session_id, turn_number, role),
            session_id=session_id,
            turn_number=turn_number,
            role=role,
            content=content,
            created_at=created_at,
            redacted=redacted,
            truncated=truncated,
        )

    @staticmethod
    def _message_params(message: ConversationMessage) -> tuple[object, ...]:
        return (
            message.message_id,
            message.session_id,
            message.turn_number,
            message.role.value,
            message.content,
            message.redacted,
            message.truncated,
            message.created_at,
        )

    async def get_snapshot(
        self,
        session_id: str,
        *,
        limit: int,
    ) -> ConversationMemorySnapshot:
        if not 1 <= limit <= MAX_HISTORY_MESSAGE_LIMIT:
            raise ValueError("limit must be between 1 and 100")
        async with self._pool.connection() as connection:
            rows = await _fetchall(
                connection,
                f"""
                WITH ranked_messages AS (
                    SELECT message.message_id,
                           message.session_id,
                           message.turn_number,
                           message.role,
                           message.content,
                           message.created_at,
                           message.redacted,
                           message.truncated,
                           COUNT(*) OVER () AS total_message_count,
                           ROW_NUMBER() OVER (
                               ORDER BY message.turn_number DESC,
                                        CASE message.role
                                            WHEN 'assistant' THEN 1 ELSE 0
                                        END DESC
                           ) AS recency_rank
                    FROM {AGENT_STATE_SCHEMA}.conversation_messages AS message
                    JOIN {AGENT_STATE_SCHEMA}.agent_sessions AS session
                      ON session.session_id = message.session_id
                    WHERE message.session_id = %s
                      AND session.deleted_at IS NULL
                )
                SELECT message_id,
                       session_id,
                       turn_number,
                       role,
                       content,
                       created_at,
                       redacted,
                       truncated,
                       total_message_count
                FROM ranked_messages
                WHERE recency_rank <= %s
                ORDER BY recency_rank DESC
                """,
                (session_id, limit),
            )
        messages = tuple(self._message_from_row(row) for row in rows)
        total = int(rows[0][8]) if rows else 0
        return ConversationMemorySnapshot(
            session_id=session_id,
            messages=messages,
            total_message_count=total,
            backend=self.backend_name,
            survives_process_restart=self.survives_process_restart,
        )

    @staticmethod
    def _message_from_row(row: Sequence[Any]) -> ConversationMessage:
        return ConversationMessage(
            message_id=str(row[0]),
            session_id=str(row[1]),
            turn_number=int(row[2]),
            role=ConversationRole(str(row[3])),
            content=str(row[4]),
            created_at=_aware_utc(row[5]),
            redacted=bool(row[6]),
            truncated=bool(row[7]),
        )

    async def clear_session(self, session_id: str) -> None:
        async with self._pool.connection() as connection:
            await _fetchone(
                connection,
                f"""
                SELECT deleted_at
                FROM {AGENT_STATE_SCHEMA}.agent_sessions
                WHERE session_id = %s
                FOR UPDATE
                """,
                (session_id,),
            )
            await connection.execute(
                f"""
                DELETE FROM {AGENT_STATE_SCHEMA}.conversation_messages
                WHERE session_id = %s
                """,
                (session_id,),
            )


__all__ = [
    "ConversationSessionUnavailableError",
    "ConversationTurnConflictError",
    "PostgresConversationMemoryError",
    "PostgresConversationMemoryStore",
]
