from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from app.agent.workflow_models import AgentSessionInfo, AgentSessionPhase
from app.persistence.sqlite_schema import (
    connect_database,
    initialize_database,
)
from app.tools.draft_models import (
    ApplicationDraft,
    DraftGenerationResult,
    DraftUserContext,
)
from app.tools.mock_approval_submission import (
    MockApprovalSubmitter,
    SubmissionConflictError,
)
from app.tools.submission_models import (
    MockApprovalSubmissionResult,
    SubmissionAuditRecord,
)

_DRAFT_RESULT_ADAPTER = TypeAdapter(DraftGenerationResult)
_SUBMISSION_RESULT_ADAPTER = TypeAdapter(
    MockApprovalSubmissionResult
)
_AUDIT_RECORD_ADAPTER = TypeAdapter(SubmissionAuditRecord)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class StoredAgentSession:
    """Minimal query model for one persisted Agent session."""

    session_id: str
    turn_number: int
    phase: AgentSessionPhase
    active_draft_id: str | None
    draft_revision: int | None
    pending_confirmation: bool
    checkpoint_backend: str
    updated_at: datetime


class SQLiteAgentStateStore:
    """Persist session metadata and versioned draft snapshots in SQLite."""

    backend_name = "sqlite"
    survives_process_restart = True

    def __init__(
        self,
        database_path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database_path = initialize_database(database_path)
        self._clock = clock or (lambda: datetime.now(UTC))

    async def save_route_state(
        self,
        session: AgentSessionInfo,
        active_draft: DraftGenerationResult | None,
    ) -> None:
        """Upsert the session head and current draft revision atomically."""

        updated_at = _aware_utc(self._clock())
        await asyncio.to_thread(
            self._save_route_state,
            session,
            active_draft,
            updated_at,
        )

    def _save_route_state(
        self,
        session: AgentSessionInfo,
        active_draft: DraftGenerationResult | None,
        updated_at: datetime,
    ) -> None:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO agent_sessions (
                    session_id,
                    turn_number,
                    phase,
                    active_draft_id,
                    draft_revision,
                    pending_confirmation,
                    checkpoint_backend,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    turn_number = excluded.turn_number,
                    phase = excluded.phase,
                    active_draft_id = excluded.active_draft_id,
                    draft_revision = excluded.draft_revision,
                    pending_confirmation = excluded.pending_confirmation,
                    checkpoint_backend = excluded.checkpoint_backend,
                    updated_at = excluded.updated_at
                """,
                (
                    session.session_id,
                    session.turn_number,
                    session.phase.value,
                    session.active_draft_id,
                    session.draft_revision,
                    int(session.pending_confirmation),
                    session.checkpoint_backend,
                    updated_at.isoformat(),
                ),
            )

            if active_draft is not None and active_draft.draft is not None:
                draft = active_draft.draft
                connection.execute(
                    """
                    INSERT INTO application_draft_snapshots (
                        draft_id,
                        revision,
                        session_id,
                        status,
                        payload_json,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(draft_id, revision) DO UPDATE SET
                        session_id = excluded.session_id,
                        status = excluded.status,
                        payload_json = excluded.payload_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        draft.draft_id,
                        draft.revision,
                        session.session_id,
                        draft.status.value,
                        _DRAFT_RESULT_ADAPTER.dump_json(
                            active_draft,
                            warnings=False,
                        ),
                        draft.audit_metadata.created_at.isoformat(),
                        updated_at.isoformat(),
                    ),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def get_session(
        self,
        session_id: str,
    ) -> StoredAgentSession | None:
        return await asyncio.to_thread(
            self._get_session,
            session_id,
        )

    def _get_session(
        self,
        session_id: str,
    ) -> StoredAgentSession | None:
        connection = connect_database(self.database_path)
        try:
            row = connection.execute(
                "SELECT * FROM agent_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            return StoredAgentSession(
                session_id=row["session_id"],
                turn_number=row["turn_number"],
                phase=AgentSessionPhase(row["phase"]),
                active_draft_id=row["active_draft_id"],
                draft_revision=row["draft_revision"],
                pending_confirmation=bool(
                    row["pending_confirmation"]
                ),
                checkpoint_backend=row["checkpoint_backend"],
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
        finally:
            connection.close()

    async def get_draft(
        self,
        draft_id: str,
        *,
        revision: int | None = None,
    ) -> DraftGenerationResult | None:
        return await asyncio.to_thread(
            self._get_draft,
            draft_id,
            revision,
        )

    def _get_draft(
        self,
        draft_id: str,
        revision: int | None,
    ) -> DraftGenerationResult | None:
        connection = connect_database(self.database_path)
        try:
            if revision is None:
                row = connection.execute(
                    """
                    SELECT payload_json
                    FROM application_draft_snapshots
                    WHERE draft_id = ?
                    ORDER BY revision DESC
                    LIMIT 1
                    """,
                    (draft_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT payload_json
                    FROM application_draft_snapshots
                    WHERE draft_id = ? AND revision = ?
                    """,
                    (draft_id, revision),
                ).fetchone()
            if row is None:
                return None
            return _DRAFT_RESULT_ADAPTER.validate_json(
                bytes(row["payload_json"])
            )
        finally:
            connection.close()

    async def list_draft_revisions(
        self,
        draft_id: str,
    ) -> tuple[int, ...]:
        return await asyncio.to_thread(
            self._list_draft_revisions,
            draft_id,
        )

    def _list_draft_revisions(
        self,
        draft_id: str,
    ) -> tuple[int, ...]:
        connection = connect_database(self.database_path)
        try:
            rows = connection.execute(
                """
                SELECT revision
                FROM application_draft_snapshots
                WHERE draft_id = ?
                ORDER BY revision
                """,
                (draft_id,),
            ).fetchall()
            return tuple(row["revision"] for row in rows)
        finally:
            connection.close()

    async def delete_session(self, session_id: str) -> None:
        await asyncio.to_thread(self._delete_session, session_id)

    def _delete_session(self, session_id: str) -> None:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM agent_sessions WHERE session_id = ?",
                (session_id,),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class SQLiteMockApprovalSubmitter(MockApprovalSubmitter):
    """Restart-safe mock approval submission with SQLite idempotency."""

    backend_name = "sqlite"
    survives_process_restart = True

    def __init__(
        self,
        database_path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(clock=clock, token_factory=token_factory)
        self.database_path = initialize_database(database_path)

    @staticmethod
    def _insert_audit(connection, audit: SubmissionAuditRecord) -> None:
        connection.execute(
            """
            INSERT INTO submission_audit_records (
                audit_id,
                submission_id,
                draft_id,
                session_id,
                event,
                payload_json,
                recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit.audit_id,
                audit.submission_id,
                audit.draft_id,
                audit.session_id,
                audit.event.value,
                _AUDIT_RECORD_ADAPTER.dump_json(audit),
                audit.recorded_at.isoformat(),
            ),
        )

    def _submit_transaction(
        self,
        draft: ApplicationDraft,
        *,
        confirmation_text: str,
        user_context: DraftUserContext,
        session_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> MockApprovalSubmissionResult:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_row = connection.execute(
                """
                SELECT draft_id, session_id, employee_id, payload_json
                FROM approval_submissions
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if existing_row is not None:
                if (
                    existing_row["draft_id"] != draft.draft_id
                    or existing_row["employee_id"]
                    != user_context.employee_id
                    or existing_row["session_id"] != session_id
                ):
                    raise SubmissionConflictError(
                        "idempotency key is already bound to another submission"
                    )
                existing = _SUBMISSION_RESULT_ADAPTER.validate_json(
                    bytes(existing_row["payload_json"])
                )
                replay = self._replayed_result(
                    existing,
                    draft,
                    confirmation_text=confirmation_text,
                    session_id=session_id,
                    request_id=request_id,
                )
                self._insert_audit(connection, replay.audit_record)
                connection.commit()
                return replay

            draft_row = connection.execute(
                """
                SELECT idempotency_key
                FROM approval_submissions
                WHERE draft_id = ?
                """,
                (draft.draft_id,),
            ).fetchone()
            if draft_row is not None:
                raise SubmissionConflictError(
                    "draft is already bound to another submission"
                )

            result = replace(
                self._first_submission_result(
                    draft,
                    user_context=user_context,
                    session_id=session_id,
                    request_id=request_id,
                    confirmation_text=confirmation_text,
                    idempotency_key=idempotency_key,
                ),
                storage_backend="sqlite",
                survives_process_restart=True,
            )
            submission = result.submission_result
            connection.execute(
                """
                INSERT INTO approval_submissions (
                    idempotency_key,
                    submission_id,
                    draft_id,
                    session_id,
                    employee_id,
                    payload_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idempotency_key,
                    submission.submission_id,
                    draft.draft_id,
                    session_id,
                    user_context.employee_id,
                    _SUBMISSION_RESULT_ADAPTER.dump_json(result),
                    submission.submitted_at.isoformat(),
                ),
            )
            self._insert_audit(connection, result.audit_record)
            connection.commit()
            return result
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def submit(
        self,
        draft: ApplicationDraft,
        *,
        confirmation_text: str,
        user_context: DraftUserContext,
        session_id: str,
        request_id: str,
        submission_idempotency_key: str,
    ) -> MockApprovalSubmissionResult:
        (
            normalized_confirmation,
            normalized_session_id,
            normalized_request_id,
            normalized_key,
        ) = self._validated_submission_request(
            draft,
            confirmation_text=confirmation_text,
            user_context=user_context,
            session_id=session_id,
            request_id=request_id,
            submission_idempotency_key=(
                submission_idempotency_key
            ),
        )
        async with self._lock:
            return await asyncio.to_thread(
                self._submit_transaction,
                draft,
                confirmation_text=normalized_confirmation,
                user_context=user_context,
                session_id=normalized_session_id,
                request_id=normalized_request_id,
                idempotency_key=normalized_key,
            )

    async def list_audit_records(
        self,
        *,
        draft_id: str | None = None,
    ) -> tuple[SubmissionAuditRecord, ...]:
        async with self._lock:
            return await asyncio.to_thread(
                self._list_audit_records,
                draft_id,
            )

    def _list_audit_records(
        self,
        draft_id: str | None,
    ) -> tuple[SubmissionAuditRecord, ...]:
        connection = connect_database(self.database_path)
        try:
            if draft_id is None:
                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM submission_audit_records
                    ORDER BY rowid
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM submission_audit_records
                    WHERE draft_id = ?
                    ORDER BY rowid
                    """,
                    (draft_id,),
                ).fetchall()
            return tuple(
                _AUDIT_RECORD_ADAPTER.validate_json(
                    bytes(row["payload_json"])
                )
                for row in rows
            )
        finally:
            connection.close()

    async def get_submission(
        self,
        *,
        draft_id: str,
    ) -> MockApprovalSubmissionResult | None:
        return await asyncio.to_thread(
            self._get_submission,
            draft_id,
        )

    def _get_submission(
        self,
        draft_id: str,
    ) -> MockApprovalSubmissionResult | None:
        connection = connect_database(self.database_path)
        try:
            row = connection.execute(
                """
                SELECT payload_json
                FROM approval_submissions
                WHERE draft_id = ?
                """,
                (draft_id,),
            ).fetchone()
            if row is None:
                return None
            return _SUBMISSION_RESULT_ADAPTER.validate_json(
                bytes(row["payload_json"])
            )
        finally:
            connection.close()
