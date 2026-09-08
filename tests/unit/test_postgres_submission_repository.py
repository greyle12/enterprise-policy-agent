from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from app.persistence.postgres_submission import PostgresMockApprovalSubmitter
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import ApplicationDraft, DraftStatus, DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker
from app.tools.mock_approval_submission import (
    MockApprovalSubmitter,
    SubmissionConflictError,
    SubmissionPreconditionError,
)
from app.tools.submission_models import (
    MockApprovalSubmissionResult,
    SubmissionAuditEvent,
    SubmissionAuditRecord,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_SESSION_ID = "postgres-submission"
_CONFIRMED_AT = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)
_SUBMITTED_AT = datetime(2026, 8, 28, 14, 30, tzinfo=UTC)
_REPLAYED_AT = datetime(2026, 8, 28, 14, 31, tzinfo=UTC)
_KEY = "postgres-submission-key-001"
_USER_CONTEXT = DraftUserContext(
    employee_id="DEMO-EMP-001",
    employee_name="演示用户",
    department="演示部门",
    roles=("EMPLOYEE",),
    region="中国大陆",
    identity_source="trusted_demo_context",
)
_COMPLETE_PURCHASE = (
    "帮我生成采购申请草稿，采购3台27英寸办公显示器，每台2000元，"
    "采购目的为给新员工配置办公设备，采购类别为IT设备，规格为27英寸2K，"
    "预算编号RD-2026，交付日期2026-09-15，使用地点苏州办公室，"
    "推荐供应商为苏州科技有限公司，推荐理由为历史合作交付稳定，普通采购，"
    "已准备技术需求说明、信息技术评审意见、产品规格说明和2家供应商报价。"
)
_SUBMISSION_ADAPTER = TypeAdapter(MockApprovalSubmissionResult)
_AUDIT_ADAPTER = TypeAdapter(SubmissionAuditRecord)


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


async def _draft() -> ApplicationDraft:
    material_checker = RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)
    approval_checker = ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)
    generator = ApplicationDraftGenerator.from_policy_directory(
        _POLICY_DIRECTORY,
        material_checker=material_checker,
        approval_checker=approval_checker,
        user_context=_USER_CONTEXT,
        clock=lambda: _CONFIRMED_AT,
    )
    answer = await generator.generate(_COMPLETE_PURCHASE, session_id=_SESSION_ID)
    assert answer.result.draft is not None
    generated = answer.result.draft
    assert generated.ready_for_confirmation
    return replace(
        generated,
        status=DraftStatus.CONFIRMED,
        confirmation_required=False,
        user_confirmed=True,
        confirmed_at=_CONFIRMED_AT,
    )


async def _stored_result(draft: ApplicationDraft) -> MockApprovalSubmissionResult:
    result = await MockApprovalSubmitter(
        clock=lambda: _SUBMITTED_AT,
        token_factory=lambda: "STORED-TOKEN",
    ).submit(
        draft,
        confirmation_text="提交审批",
        user_context=_USER_CONTEXT,
        session_id=_SESSION_ID,
        request_id="STORED-REQUEST",
        submission_idempotency_key=_KEY,
    )
    return replace(
        result,
        storage_backend="postgresql",
        survives_process_restart=True,
    )


def _submission_payload(result: MockApprovalSubmissionResult) -> dict[str, object]:
    payload = _SUBMISSION_ADAPTER.dump_python(result, mode="json", warnings=False)
    assert isinstance(payload, dict)
    return payload


def _audit_payload(record: SubmissionAuditRecord) -> dict[str, object]:
    payload = _AUDIT_ADAPTER.dump_python(record, mode="json", warnings=False)
    assert isinstance(payload, dict)
    return payload


async def _submit(
    submitter: PostgresMockApprovalSubmitter,
    draft: ApplicationDraft,
    *,
    key: str = _KEY,
    request_id: str = "POSTGRES-REQUEST",
) -> MockApprovalSubmissionResult:
    return await submitter.submit(
        draft,
        confirmation_text="提交审批",
        user_context=_USER_CONTEXT,
        session_id=_SESSION_ID,
        request_id=request_id,
        submission_idempotency_key=key,
    )


@pytest.mark.asyncio
async def test_first_submission_writes_receipt_and_audit_in_one_transaction() -> None:
    draft = await _draft()
    pool = _Pool(
        (
            _ExpectedExecution("WHERE idempotency_key = %s FOR UPDATE"),
            _ExpectedExecution("WHERE draft_id = %s FOR UPDATE"),
            _ExpectedExecution("INSERT INTO agent_runtime.approval_submissions", one=("created",)),
            _ExpectedExecution(
                "INSERT INTO agent_runtime.submission_audit_records",
                one=("audit-created",),
            ),
        )
    )

    result = await _submit(
        PostgresMockApprovalSubmitter(
            pool=pool,
            clock=lambda: _SUBMITTED_AT,
            token_factory=lambda: "FIRST-TOKEN",
        ),
        draft,
    )

    assert result.duplicate_submission is False
    assert result.storage_backend == "postgresql"
    assert result.survives_process_restart is True
    assert result.audit_record.event is SubmissionAuditEvent.SUBMITTED
    receipt_sql, receipt_params = pool.connection_instance.executed[2]
    assert "ON CONFLICT DO NOTHING" in receipt_sql
    assert isinstance(receipt_params[5], Jsonb)
    assert receipt_params[5].obj["storage_backend"] == "postgresql"
    _, audit_params = pool.connection_instance.executed[3]
    assert isinstance(audit_params[5], Jsonb)
    assert "confirmation_text" not in audit_params[5].obj
    assert pool.commits == 1
    assert pool.rollbacks == 0
    pool.assert_consumed()


@pytest.mark.asyncio
async def test_existing_idempotency_key_returns_original_receipt_and_appends_replay_audit() -> None:
    draft = await _draft()
    stored = await _stored_result(draft)
    pool = _Pool(
        (
            _ExpectedExecution(
                "WHERE idempotency_key = %s FOR UPDATE",
                one=(
                    draft.draft_id,
                    _SESSION_ID,
                    _USER_CONTEXT.employee_id,
                    _submission_payload(stored),
                ),
            ),
            _ExpectedExecution(
                "INSERT INTO agent_runtime.submission_audit_records",
                one=("replay-audit",),
            ),
        )
    )

    replay = await _submit(
        PostgresMockApprovalSubmitter(
            pool=pool,
            clock=lambda: _REPLAYED_AT,
            token_factory=lambda: "REPLAY-TOKEN",
        ),
        draft,
        request_id="REPLAY-REQUEST",
    )

    assert replay.duplicate_submission is True
    assert replay.submission_result == stored.submission_result
    assert replay.approval_workflow == stored.approval_workflow
    assert replay.audit_record.event is SubmissionAuditEvent.IDEMPOTENT_REPLAY
    assert replay.audit_record.request_id == "REPLAY-REQUEST"
    assert pool.commits == 1
    pool.assert_consumed()


@pytest.mark.asyncio
async def test_rejects_idempotency_key_bound_to_another_identity() -> None:
    draft = await _draft()
    stored = await _stored_result(draft)
    pool = _Pool(
        (
            _ExpectedExecution(
                "WHERE idempotency_key = %s FOR UPDATE",
                one=(
                    draft.draft_id,
                    "other-session",
                    "OTHER-EMPLOYEE",
                    _submission_payload(stored),
                ),
            ),
        )
    )

    with pytest.raises(SubmissionConflictError, match="another submission"):
        await _submit(PostgresMockApprovalSubmitter(pool=pool), draft)

    assert pool.rollbacks == 1
    pool.assert_consumed()


@pytest.mark.asyncio
async def test_rejects_submission_column_and_payload_drift() -> None:
    draft = await _draft()
    stored = await _stored_result(draft)
    drifted = replace(stored, storage_backend="sqlite")
    pool = _Pool(
        (
            _ExpectedExecution(
                "WHERE idempotency_key = %s FOR UPDATE",
                one=(
                    draft.draft_id,
                    _SESSION_ID,
                    _USER_CONTEXT.employee_id,
                    _submission_payload(drifted),
                ),
            ),
        )
    )

    with pytest.raises(SubmissionConflictError, match="payload have drifted"):
        await _submit(PostgresMockApprovalSubmitter(pool=pool), draft)

    assert pool.rollbacks == 1
    pool.assert_consumed()


@pytest.mark.asyncio
async def test_rejects_same_draft_with_a_different_key() -> None:
    draft = await _draft()
    pool = _Pool(
        (
            _ExpectedExecution("WHERE idempotency_key = %s FOR UPDATE"),
            _ExpectedExecution("WHERE draft_id = %s FOR UPDATE", one=(_KEY,)),
        )
    )

    with pytest.raises(SubmissionConflictError, match="draft is already bound"):
        await _submit(
            PostgresMockApprovalSubmitter(pool=pool),
            draft,
            key="postgres-different-key-001",
        )

    assert pool.rollbacks == 1
    pool.assert_consumed()


@pytest.mark.asyncio
async def test_lost_insert_race_reloads_winner_and_returns_replay() -> None:
    draft = await _draft()
    stored = await _stored_result(draft)
    times = iter((_SUBMITTED_AT, _REPLAYED_AT))
    pool = _Pool(
        (
            _ExpectedExecution("WHERE idempotency_key = %s FOR UPDATE"),
            _ExpectedExecution("WHERE draft_id = %s FOR UPDATE"),
            _ExpectedExecution("INSERT INTO agent_runtime.approval_submissions"),
            _ExpectedExecution(
                "WHERE idempotency_key = %s FOR UPDATE",
                one=(
                    draft.draft_id,
                    _SESSION_ID,
                    _USER_CONTEXT.employee_id,
                    _submission_payload(stored),
                ),
            ),
            _ExpectedExecution(
                "INSERT INTO agent_runtime.submission_audit_records",
                one=("race-replay-audit",),
            ),
        )
    )

    replay = await _submit(
        PostgresMockApprovalSubmitter(
            pool=pool,
            clock=lambda: next(times),
            token_factory=lambda: "RACE-TOKEN",
        ),
        draft,
    )

    assert replay.duplicate_submission is True
    assert replay.submission_result == stored.submission_result
    assert replay.audit_record.recorded_at == _REPLAYED_AT
    assert pool.commits == 1
    pool.assert_consumed()


@pytest.mark.asyncio
async def test_lost_insert_race_detects_draft_bound_by_other_key() -> None:
    draft = await _draft()
    pool = _Pool(
        (
            _ExpectedExecution("WHERE idempotency_key = %s FOR UPDATE"),
            _ExpectedExecution("WHERE draft_id = %s FOR UPDATE"),
            _ExpectedExecution("INSERT INTO agent_runtime.approval_submissions"),
            _ExpectedExecution("WHERE idempotency_key = %s FOR UPDATE"),
            _ExpectedExecution("WHERE draft_id = %s FOR UPDATE", one=("winner-key",)),
        )
    )

    with pytest.raises(SubmissionConflictError, match="draft is already bound"):
        await _submit(
            PostgresMockApprovalSubmitter(
                pool=pool,
                clock=lambda: _SUBMITTED_AT,
                token_factory=lambda: "LOSER-TOKEN",
            ),
            draft,
        )

    assert pool.rollbacks == 1
    pool.assert_consumed()


@pytest.mark.asyncio
async def test_audit_identifier_collision_rolls_back_first_submission() -> None:
    draft = await _draft()
    pool = _Pool(
        (
            _ExpectedExecution("WHERE idempotency_key = %s FOR UPDATE"),
            _ExpectedExecution("WHERE draft_id = %s FOR UPDATE"),
            _ExpectedExecution("INSERT INTO agent_runtime.approval_submissions", one=("created",)),
            _ExpectedExecution("INSERT INTO agent_runtime.submission_audit_records"),
        )
    )

    with pytest.raises(SubmissionConflictError, match="audit identifier"):
        await _submit(
            PostgresMockApprovalSubmitter(
                pool=pool,
                clock=lambda: _SUBMITTED_AT,
                token_factory=lambda: "AUDIT-COLLISION",
            ),
            draft,
        )

    assert pool.commits == 0
    assert pool.rollbacks == 1
    pool.assert_consumed()


@pytest.mark.asyncio
async def test_lists_ordered_audit_records_and_reads_submission_by_draft() -> None:
    draft = await _draft()
    stored = await _stored_result(draft)
    first_audit = stored.audit_record
    replay_audit = replace(
        first_audit,
        audit_id="AUDIT-REPLAY",
        event=SubmissionAuditEvent.IDEMPOTENT_REPLAY,
        recorded_at=_REPLAYED_AT,
        duplicate_submission=True,
    )
    pool = _Pool(
        (
            _ExpectedExecution(
                "FROM agent_runtime.submission_audit_records WHERE draft_id = %s",
                rows=((_audit_payload(first_audit),), (_audit_payload(replay_audit),)),
            ),
            _ExpectedExecution(
                "FROM agent_runtime.approval_submissions WHERE draft_id = %s",
                one=(_submission_payload(stored),),
            ),
        )
    )
    repository = PostgresMockApprovalSubmitter(pool=pool)

    audits = await repository.list_audit_records(draft_id=draft.draft_id)
    submission = await repository.get_submission(draft_id=draft.draft_id)

    assert [record.event for record in audits] == [
        SubmissionAuditEvent.SUBMITTED,
        SubmissionAuditEvent.IDEMPOTENT_REPLAY,
    ]
    assert submission is not None
    assert _submission_payload(submission) == _submission_payload(stored)
    first_query, first_params = pool.connection_instance.executed[0]
    assert "ORDER BY recorded_at, audit_id" in first_query
    assert first_params == (draft.draft_id,)
    assert pool.commits == 2
    pool.assert_consumed()


@pytest.mark.asyncio
async def test_preconditions_fail_before_database_access() -> None:
    draft = await _draft()
    pool = _Pool(())

    with pytest.raises(SubmissionPreconditionError) as exc_info:
        await PostgresMockApprovalSubmitter(pool=pool).submit(
            draft,
            confirmation_text="可以提交吗？",
            user_context=_USER_CONTEXT,
            session_id=_SESSION_ID,
            request_id="AMBIGUOUS",
            submission_idempotency_key=_KEY,
        )

    assert exc_info.value.code == "explicit_submission_required"
    assert pool.commits == 0
    assert pool.rollbacks == 0


def test_postgres_submitter_reports_shared_durable_backend() -> None:
    assert PostgresMockApprovalSubmitter.backend_name == "postgresql"
    assert PostgresMockApprovalSubmitter.survives_process_restart is True
