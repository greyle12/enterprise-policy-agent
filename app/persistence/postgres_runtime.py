from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from app.agent.workflow_models import AgentSessionInfo, AgentSessionPhase
from app.persistence.postgres_connection import (
    PostgresStateConnection,
    PostgresStateConnectionPool,
)
from app.persistence.postgres_schema import (
    AGENT_STATE_MIGRATION_TABLE,
    AGENT_STATE_SCHEMA,
    AGENT_STATE_SCHEMA_VERSION,
)
from app.persistence.state_models import StoredAgentSession
from app.tools.draft_models import (
    ApplicationDraft,
    DraftGenerationResult,
    DraftStatus,
)

_DRAFT_RESULT_ADAPTER = TypeAdapter(DraftGenerationResult)
_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
_ALLOWED_DRAFT_STATUS_TRANSITIONS: dict[DraftStatus, frozenset[DraftStatus]] = {
    DraftStatus.WAITING_FOR_INFORMATION: frozenset({DraftStatus.CANCELLED}),
    DraftStatus.WAITING_FOR_MATERIALS: frozenset({DraftStatus.CANCELLED}),
    DraftStatus.WAITING_FOR_CONFIRMATION: frozenset({DraftStatus.CONFIRMED, DraftStatus.CANCELLED}),
    DraftStatus.CONFIRMED: frozenset({DraftStatus.SUBMITTED, DraftStatus.CANCELLED}),
    DraftStatus.SUBMITTED: frozenset(),
    DraftStatus.CANCELLED: frozenset(),
}


class PostgresAgentStateConflictError(RuntimeError):
    """Base error for a rejected PostgreSQL Agent projection mutation."""


class SessionStateConflictError(PostgresAgentStateConflictError):
    """Raised when a stale or divergent session head loses its CAS check."""


class SessionDeletedError(SessionStateConflictError):
    """Raised when a mutation attempts to revive a tombstoned session."""


class DraftRevisionConflictError(PostgresAgentStateConflictError):
    """Raised when an existing draft revision would be replaced unsafely."""


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


def _draft_payload(result: DraftGenerationResult) -> object:
    return _DRAFT_RESULT_ADAPTER.dump_python(
        result,
        mode="json",
        warnings=False,
    )


def _decode_draft_payload(value: object) -> DraftGenerationResult:
    if isinstance(value, (str, bytes, bytearray)):
        return _DRAFT_RESULT_ADAPTER.validate_json(value)
    if isinstance(value, Mapping):
        return _DRAFT_RESULT_ADAPTER.validate_python(value)
    raise DraftRevisionConflictError("stored draft payload is not valid JSONB")


def _draft_content_identity(result: DraftGenerationResult) -> object:
    """Return a JSON-normalized view of immutable content for one revision."""

    payload = _draft_payload(result)
    if not isinstance(payload, Mapping):
        raise DraftRevisionConflictError("draft result payload is not a JSON object")
    draft_payload = payload.get("draft")
    if not isinstance(draft_payload, Mapping):
        raise DraftRevisionConflictError("stored draft revision is missing its draft payload")
    immutable_draft = dict(draft_payload)
    for field_name in (
        "status",
        "ready_for_confirmation",
        "confirmation_required",
        "user_confirmed",
        "submitted",
        "confirmed_at",
        "cancelled_at",
        "submission_id",
        "submitted_at",
    ):
        immutable_draft.pop(field_name, None)
    return (
        payload.get("application_type"),
        payload.get("citations"),
        immutable_draft,
    )


def _validate_lifecycle_shape(draft: ApplicationDraft) -> None:
    if draft.status is DraftStatus.CONFIRMED:
        valid = (
            draft.user_confirmed
            and draft.confirmed_at is not None
            and not draft.submitted
            and draft.submission_id is None
            and draft.submitted_at is None
            and draft.cancelled_at is None
        )
    elif draft.status is DraftStatus.SUBMITTED:
        valid = (
            draft.user_confirmed
            and draft.confirmed_at is not None
            and draft.submitted
            and bool(draft.submission_id)
            and draft.submitted_at is not None
            and draft.cancelled_at is None
        )
    elif draft.status is DraftStatus.CANCELLED:
        valid = (
            not draft.user_confirmed
            and not draft.submitted
            and draft.submission_id is None
            and draft.submitted_at is None
            and draft.cancelled_at is not None
        )
    else:
        valid = not draft.user_confirmed and not draft.submitted
    if not valid:
        raise DraftRevisionConflictError(
            f"draft lifecycle fields are inconsistent with status {draft.status.value}"
        )


def _validate_draft_transition(
    existing: DraftGenerationResult,
    incoming: DraftGenerationResult,
) -> None:
    existing_draft = existing.draft
    incoming_draft = incoming.draft
    if existing_draft is None or incoming_draft is None:
        raise DraftRevisionConflictError("draft revision payload must contain a draft")
    if _draft_content_identity(existing) != _draft_content_identity(incoming):
        raise DraftRevisionConflictError(
            "draft business content cannot change inside an existing revision"
        )
    allowed = _ALLOWED_DRAFT_STATUS_TRANSITIONS[existing_draft.status]
    if incoming_draft.status not in allowed:
        raise DraftRevisionConflictError(
            "draft lifecycle transition is not monotonic: "
            f"{existing_draft.status.value} -> {incoming_draft.status.value}"
        )
    _validate_lifecycle_shape(incoming_draft)


class PostgresAgentStateStore:
    """PostgreSQL session head and versioned draft projection repository."""

    backend_name = "postgresql"
    survives_process_restart = True

    def __init__(
        self,
        *,
        pool: PostgresStateConnectionPool,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._pool = pool
        self._clock = clock or (lambda: datetime.now(UTC))

    async def ping(self) -> None:
        """Verify PostgreSQL access and the exact Agent schema version."""

        async with self._pool.connection() as connection:
            ready_row = await _fetchone(connection, "SELECT 1")
            if ready_row is None or int(ready_row[0]) != 1:
                raise RuntimeError("PostgreSQL readiness query returned no result")
            version_row = await _fetchone(
                connection,
                f"SELECT COALESCE(MAX(version), 0) FROM {AGENT_STATE_MIGRATION_TABLE}",
            )
            current_version = int(version_row[0]) if version_row is not None else 0
            if current_version != AGENT_STATE_SCHEMA_VERSION:
                raise RuntimeError(
                    "PostgreSQL Agent state schema version does not match the application: "
                    f"{current_version} != {AGENT_STATE_SCHEMA_VERSION}"
                )

    async def save_route_state(
        self,
        session: AgentSessionInfo,
        active_draft: DraftGenerationResult | None,
    ) -> None:
        """Atomically save a monotonic session head and one guarded draft revision."""

        draft = self._validate_route_state(session, active_draft)
        updated_at = _aware_utc(self._clock())
        async with self._pool.connection() as connection:
            await self._save_session_head(connection, session, updated_at)
            if draft is not None and active_draft is not None:
                await self._save_draft_revision(
                    connection,
                    session_id=session.session_id,
                    result=active_draft,
                    draft=draft,
                    updated_at=updated_at,
                )

    @staticmethod
    def _validate_route_state(
        session: AgentSessionInfo,
        active_draft: DraftGenerationResult | None,
    ) -> ApplicationDraft | None:
        if not _SESSION_ID_PATTERN.fullmatch(session.session_id):
            raise ValueError("session_id must use the Agent 1-64 character identifier format")
        if session.turn_number < 0:
            raise ValueError("turn_number must not be negative")
        if not session.checkpoint_backend.strip():
            raise ValueError("checkpoint_backend must not be blank")
        if (session.active_draft_id is None) != (session.draft_revision is None):
            raise ValueError("active_draft_id and draft_revision must appear together")

        draft = active_draft.draft if active_draft is not None else None
        if draft is None:
            if session.active_draft_id is not None:
                raise ValueError(
                    "session references a draft but no active draft payload was provided"
                )
            return None
        if session.active_draft_id != draft.draft_id or session.draft_revision != draft.revision:
            raise ValueError("session draft head does not match the active draft payload")
        if draft.audit_metadata.session_id != session.session_id:
            raise ValueError("active draft belongs to another session")
        if draft.revision < 1:
            raise ValueError("draft revision must be positive")
        _validate_lifecycle_shape(draft)
        return draft

    async def _save_session_head(
        self,
        connection: PostgresStateConnection,
        session: AgentSessionInfo,
        updated_at: datetime,
    ) -> None:
        inserted = await _fetchone(
            connection,
            f"""
            INSERT INTO {AGENT_STATE_SCHEMA}.agent_sessions (
                session_id,
                turn_number,
                phase,
                active_draft_id,
                draft_revision,
                pending_confirmation,
                checkpoint_backend,
                state_version,
                updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s)
            ON CONFLICT (session_id) DO NOTHING
            RETURNING state_version
            """,
            (
                session.session_id,
                session.turn_number,
                session.phase.value,
                session.active_draft_id,
                session.draft_revision,
                session.pending_confirmation,
                session.checkpoint_backend,
                updated_at,
            ),
        )
        if inserted is not None:
            return

        current = await _fetchone(
            connection,
            f"""
            SELECT turn_number, phase, active_draft_id, draft_revision,
                   pending_confirmation, checkpoint_backend, state_version, deleted_at
            FROM {AGENT_STATE_SCHEMA}.agent_sessions
            WHERE session_id = %s
            FOR UPDATE
            """,
            (session.session_id,),
        )
        if current is None:
            raise SessionStateConflictError("session row disappeared during conflict handling")
        if current[7] is not None:
            raise SessionDeletedError("tombstoned session cannot be revived")

        current_turn = int(current[0])
        current_state = (
            current_turn,
            str(current[1]),
            current[2],
            (None if current[3] is None else int(current[3])),
            bool(current[4]),
            str(current[5]),
        )
        incoming_state = (
            session.turn_number,
            session.phase.value,
            session.active_draft_id,
            session.draft_revision,
            session.pending_confirmation,
            session.checkpoint_backend,
        )
        if session.turn_number < current_turn:
            raise SessionStateConflictError(
                f"stale session turn {session.turn_number} cannot replace turn {current_turn}"
            )
        if session.turn_number == current_turn:
            if incoming_state != current_state:
                raise SessionStateConflictError(
                    "the same session turn is already bound to different projection state"
                )
            return

        expected_version = int(current[6])
        updated = await _fetchone(
            connection,
            f"""
            UPDATE {AGENT_STATE_SCHEMA}.agent_sessions
            SET turn_number = %s,
                phase = %s,
                active_draft_id = %s,
                draft_revision = %s,
                pending_confirmation = %s,
                checkpoint_backend = %s,
                state_version = state_version + 1,
                updated_at = %s
            WHERE session_id = %s
              AND state_version = %s
              AND deleted_at IS NULL
            RETURNING state_version
            """,
            (
                session.turn_number,
                session.phase.value,
                session.active_draft_id,
                session.draft_revision,
                session.pending_confirmation,
                session.checkpoint_backend,
                updated_at,
                session.session_id,
                expected_version,
            ),
        )
        if updated is None:
            raise SessionStateConflictError("session state_version CAS update was lost")

    async def _save_draft_revision(
        self,
        connection: PostgresStateConnection,
        *,
        session_id: str,
        result: DraftGenerationResult,
        draft: ApplicationDraft,
        updated_at: datetime,
    ) -> None:
        payload = _draft_payload(result)
        inserted = await _fetchone(
            connection,
            f"""
            INSERT INTO {AGENT_STATE_SCHEMA}.application_draft_snapshots (
                draft_id,
                revision,
                session_id,
                status,
                payload_json,
                created_at,
                updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (draft_id, revision) DO NOTHING
            RETURNING draft_id
            """,
            (
                draft.draft_id,
                draft.revision,
                session_id,
                draft.status.value,
                Jsonb(payload),
                _aware_utc(draft.audit_metadata.created_at),
                updated_at,
            ),
        )
        if inserted is not None:
            return

        current = await _fetchone(
            connection,
            f"""
            SELECT session_id, status, payload_json
            FROM {AGENT_STATE_SCHEMA}.application_draft_snapshots
            WHERE draft_id = %s AND revision = %s
            FOR UPDATE
            """,
            (draft.draft_id, draft.revision),
        )
        if current is None:
            raise DraftRevisionConflictError("draft revision disappeared during conflict handling")
        if str(current[0]) != session_id:
            raise DraftRevisionConflictError("draft revision belongs to another session")

        existing = _decode_draft_payload(current[2])
        existing_draft = existing.draft
        if existing_draft is None or str(current[1]) != existing_draft.status.value:
            raise DraftRevisionConflictError("draft status column and payload have drifted")
        if _draft_payload(existing) == payload:
            return
        _validate_draft_transition(existing, result)

        updated = await _fetchone(
            connection,
            f"""
            UPDATE {AGENT_STATE_SCHEMA}.application_draft_snapshots
            SET status = %s,
                payload_json = %s,
                updated_at = %s
            WHERE draft_id = %s
              AND revision = %s
              AND session_id = %s
              AND status = %s
            RETURNING draft_id
            """,
            (
                draft.status.value,
                Jsonb(payload),
                updated_at,
                draft.draft_id,
                draft.revision,
                session_id,
                existing_draft.status.value,
            ),
        )
        if updated is None:
            raise DraftRevisionConflictError("draft lifecycle CAS update was lost")

    async def get_session(self, session_id: str) -> StoredAgentSession | None:
        async with self._pool.connection() as connection:
            row = await _fetchone(
                connection,
                f"""
                SELECT session_id, turn_number, phase, active_draft_id,
                       draft_revision, pending_confirmation, checkpoint_backend, updated_at
                FROM {AGENT_STATE_SCHEMA}.agent_sessions
                WHERE session_id = %s AND deleted_at IS NULL
                """,
                (session_id,),
            )
        if row is None:
            return None
        return StoredAgentSession(
            session_id=str(row[0]),
            turn_number=int(row[1]),
            phase=AgentSessionPhase(str(row[2])),
            active_draft_id=(None if row[3] is None else str(row[3])),
            draft_revision=(None if row[4] is None else int(row[4])),
            pending_confirmation=bool(row[5]),
            checkpoint_backend=str(row[6]),
            updated_at=_aware_utc(row[7]),
        )

    async def get_draft(
        self,
        draft_id: str,
        *,
        revision: int | None = None,
    ) -> DraftGenerationResult | None:
        async with self._pool.connection() as connection:
            if revision is None:
                row = await _fetchone(
                    connection,
                    f"""
                    SELECT draft.payload_json
                    FROM {AGENT_STATE_SCHEMA}.application_draft_snapshots AS draft
                    JOIN {AGENT_STATE_SCHEMA}.agent_sessions AS session
                      ON session.session_id = draft.session_id
                    WHERE draft.draft_id = %s AND session.deleted_at IS NULL
                    ORDER BY draft.revision DESC
                    LIMIT 1
                    """,
                    (draft_id,),
                )
            else:
                row = await _fetchone(
                    connection,
                    f"""
                    SELECT draft.payload_json
                    FROM {AGENT_STATE_SCHEMA}.application_draft_snapshots AS draft
                    JOIN {AGENT_STATE_SCHEMA}.agent_sessions AS session
                      ON session.session_id = draft.session_id
                    WHERE draft.draft_id = %s
                      AND draft.revision = %s
                      AND session.deleted_at IS NULL
                    """,
                    (draft_id, revision),
                )
        return None if row is None else _decode_draft_payload(row[0])

    async def list_draft_revisions(self, draft_id: str) -> tuple[int, ...]:
        async with self._pool.connection() as connection:
            rows = await _fetchall(
                connection,
                f"""
                SELECT draft.revision
                FROM {AGENT_STATE_SCHEMA}.application_draft_snapshots AS draft
                JOIN {AGENT_STATE_SCHEMA}.agent_sessions AS session
                  ON session.session_id = draft.session_id
                WHERE draft.draft_id = %s AND session.deleted_at IS NULL
                ORDER BY draft.revision
                """,
                (draft_id,),
            )
        return tuple(int(row[0]) for row in rows)

    async def delete_session(self, session_id: str) -> None:
        """Tombstone a session and remove only its mutable draft projections."""

        deleted_at = _aware_utc(self._clock())
        async with self._pool.connection() as connection:
            current = await _fetchone(
                connection,
                f"""
                SELECT state_version, deleted_at
                FROM {AGENT_STATE_SCHEMA}.agent_sessions
                WHERE session_id = %s
                FOR UPDATE
                """,
                (session_id,),
            )
            if current is None:
                return
            if current[1] is None:
                updated = await _fetchone(
                    connection,
                    f"""
                    UPDATE {AGENT_STATE_SCHEMA}.agent_sessions
                    SET deleted_at = %s,
                        updated_at = %s,
                        state_version = state_version + 1
                    WHERE session_id = %s
                      AND state_version = %s
                      AND deleted_at IS NULL
                    RETURNING state_version
                    """,
                    (deleted_at, deleted_at, session_id, int(current[0])),
                )
                if updated is None:
                    raise SessionStateConflictError("session tombstone CAS update was lost")
            await connection.execute(
                f"""
                DELETE FROM {AGENT_STATE_SCHEMA}.application_draft_snapshots
                WHERE session_id = %s
                """,
                (session_id,),
            )


__all__ = [
    "DraftRevisionConflictError",
    "PostgresAgentStateConflictError",
    "PostgresAgentStateStore",
    "SessionDeletedError",
    "SessionStateConflictError",
]
