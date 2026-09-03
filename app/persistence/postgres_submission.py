from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from app.persistence.postgres_connection import (
    PostgresStateConnection,
    PostgresStateConnectionPool,
)
from app.persistence.postgres_schema import AGENT_STATE_SCHEMA
from app.tools.draft_models import ApplicationDraft, DraftUserContext
from app.tools.mock_approval_submission import (
    MockApprovalSubmitter,
    SubmissionConflictError,
)
from app.tools.submission_models import (
    MockApprovalSubmissionResult,
    SubmissionAuditRecord,
)

_SUBMISSION_RESULT_ADAPTER = TypeAdapter(MockApprovalSubmissionResult)
_AUDIT_RECORD_ADAPTER = TypeAdapter(SubmissionAuditRecord)


def _submission_payload(result: MockApprovalSubmissionResult) -> object:
    return _SUBMISSION_RESULT_ADAPTER.dump_python(result, mode="json", warnings=False)


def _audit_payload(record: SubmissionAuditRecord) -> object:
    return _AUDIT_RECORD_ADAPTER.dump_python(record, mode="json", warnings=False)


def _decode_submission(value: object) -> MockApprovalSubmissionResult:
    if isinstance(value, (str, bytes, bytearray)):
        return _SUBMISSION_RESULT_ADAPTER.validate_json(value)
    if isinstance(value, Mapping):
        return _SUBMISSION_RESULT_ADAPTER.validate_python(value)
    raise SubmissionConflictError("stored submission payload is not valid JSONB")


def _decode_audit(value: object) -> SubmissionAuditRecord:
    if isinstance(value, (str, bytes, bytearray)):
        return _AUDIT_RECORD_ADAPTER.validate_json(value)
    if isinstance(value, Mapping):
        return _AUDIT_RECORD_ADAPTER.validate_python(value)
    raise SubmissionConflictError("stored audit payload is not valid JSONB")


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


class PostgresMockApprovalSubmitter(MockApprovalSubmitter):
    """PostgreSQL-backed mock submission receipt and append-only audit repository."""

    backend_name = "postgresql"
    survives_process_restart = True

    def __init__(
        self,
        *,
        pool: PostgresStateConnectionPool,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(clock=clock, token_factory=token_factory)
        self._pool = pool

    @staticmethod
    async def _find_by_idempotency_key(
        connection: PostgresStateConnection,
        idempotency_key: str,
    ) -> Sequence[Any] | None:
        return await _fetchone(
            connection,
            f"""
            SELECT draft_id, session_id, employee_id, payload_json
            FROM {AGENT_STATE_SCHEMA}.approval_submissions
            WHERE idempotency_key = %s
            FOR UPDATE
            """,
            (idempotency_key,),
        )

    @staticmethod
    async def _find_draft_binding(
        connection: PostgresStateConnection,
        draft_id: str,
    ) -> Sequence[Any] | None:
        return await _fetchone(
            connection,
            f"""
            SELECT idempotency_key
            FROM {AGENT_STATE_SCHEMA}.approval_submissions
            WHERE draft_id = %s
            FOR UPDATE
            """,
            (draft_id,),
        )

    @staticmethod
    def _validated_existing_result(
        row: Sequence[Any],
        *,
        draft: ApplicationDraft,
        user_context: DraftUserContext,
        session_id: str,
        idempotency_key: str,
    ) -> MockApprovalSubmissionResult:
        if (
            str(row[0]) != draft.draft_id
            or str(row[1]) != session_id
            or str(row[2]) != user_context.employee_id
        ):
            raise SubmissionConflictError("idempotency key is already bound to another submission")
        existing = _decode_submission(row[3])
        submission = existing.submission_result
        if (
            submission.draft_id != draft.draft_id
            or submission.submitted_by != user_context.employee_id
            or submission.idempotency_key != idempotency_key
            or existing.storage_backend != "postgresql"
            or not existing.survives_process_restart
        ):
            raise SubmissionConflictError("submission columns and persisted payload have drifted")
        return existing

    async def _insert_audit(
        self,
        connection: PostgresStateConnection,
        audit: SubmissionAuditRecord,
    ) -> None:
        inserted = await _fetchone(
            connection,
            f"""
            INSERT INTO {AGENT_STATE_SCHEMA}.submission_audit_records (
                audit_id,
                submission_id,
                draft_id,
                session_id,
                event,
                payload_json,
                recorded_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (audit_id) DO NOTHING
            RETURNING audit_id
            """,
            (
                audit.audit_id,
                audit.submission_id,
                audit.draft_id,
                audit.session_id,
                audit.event.value,
                Jsonb(_audit_payload(audit)),
                audit.recorded_at,
            ),
        )
        if inserted is None:
            raise SubmissionConflictError("submission audit identifier already exists")

    async def _replay_existing(
        self,
        connection: PostgresStateConnection,
        row: Sequence[Any],
        draft: ApplicationDraft,
        *,
        confirmation_text: str,
        user_context: DraftUserContext,
        session_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> MockApprovalSubmissionResult:
        existing = self._validated_existing_result(
            row,
            draft=draft,
            user_context=user_context,
            session_id=session_id,
            idempotency_key=idempotency_key,
        )
        replay = self._replayed_result(
            existing,
            draft,
            confirmation_text=confirmation_text,
            session_id=session_id,
            request_id=request_id,
        )
        await self._insert_audit(connection, replay.audit_record)
        return replay

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
            submission_idempotency_key=submission_idempotency_key,
        )

        async with self._pool.connection() as connection:
            existing = await self._find_by_idempotency_key(connection, normalized_key)
            if existing is not None:
                return await self._replay_existing(
                    connection,
                    existing,
                    draft,
                    confirmation_text=normalized_confirmation,
                    user_context=user_context,
                    session_id=normalized_session_id,
                    request_id=normalized_request_id,
                    idempotency_key=normalized_key,
                )

            draft_binding = await self._find_draft_binding(connection, draft.draft_id)
            if draft_binding is not None:
                raise SubmissionConflictError("draft is already bound to another submission")

            result = replace(
                self._first_submission_result(
                    draft,
                    user_context=user_context,
                    session_id=normalized_session_id,
                    request_id=normalized_request_id,
                    confirmation_text=normalized_confirmation,
                    idempotency_key=normalized_key,
                ),
                storage_backend=self.backend_name,
                survives_process_restart=self.survives_process_restart,
            )
            submission = result.submission_result
            inserted = await _fetchone(
                connection,
                f"""
                INSERT INTO {AGENT_STATE_SCHEMA}.approval_submissions (
                    idempotency_key,
                    submission_id,
                    draft_id,
                    session_id,
                    employee_id,
                    payload_json,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING submission_id
                """,
                (
                    normalized_key,
                    submission.submission_id,
                    draft.draft_id,
                    normalized_session_id,
                    user_context.employee_id,
                    Jsonb(_submission_payload(result)),
                    submission.submitted_at,
                ),
            )
            if inserted is not None:
                await self._insert_audit(connection, result.audit_record)
                return result

            concurrent = await self._find_by_idempotency_key(connection, normalized_key)
            if concurrent is not None:
                return await self._replay_existing(
                    connection,
                    concurrent,
                    draft,
                    confirmation_text=normalized_confirmation,
                    user_context=user_context,
                    session_id=normalized_session_id,
                    request_id=normalized_request_id,
                    idempotency_key=normalized_key,
                )
            if await self._find_draft_binding(connection, draft.draft_id) is not None:
                raise SubmissionConflictError("draft is already bound to another submission")
            raise SubmissionConflictError("submission identifier collided with an existing receipt")

    async def list_audit_records(
        self,
        *,
        draft_id: str | None = None,
    ) -> tuple[SubmissionAuditRecord, ...]:
        async with self._pool.connection() as connection:
            if draft_id is None:
                rows = await _fetchall(
                    connection,
                    f"""
                    SELECT payload_json
                    FROM {AGENT_STATE_SCHEMA}.submission_audit_records
                    ORDER BY recorded_at, audit_id
                    """,
                )
            else:
                rows = await _fetchall(
                    connection,
                    f"""
                    SELECT payload_json
                    FROM {AGENT_STATE_SCHEMA}.submission_audit_records
                    WHERE draft_id = %s
                    ORDER BY recorded_at, audit_id
                    """,
                    (draft_id,),
                )
        return tuple(_decode_audit(row[0]) for row in rows)

    async def get_submission(
        self,
        *,
        draft_id: str,
    ) -> MockApprovalSubmissionResult | None:
        async with self._pool.connection() as connection:
            row = await _fetchone(
                connection,
                f"""
                SELECT payload_json
                FROM {AGENT_STATE_SCHEMA}.approval_submissions
                WHERE draft_id = %s
                """,
                (draft_id,),
            )
        return None if row is None else _decode_submission(row[0])


__all__ = ["PostgresMockApprovalSubmitter"]
