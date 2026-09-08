from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import AsyncConnectionPool

from app.agent.workflow_models import AgentSessionInfo, AgentSessionPhase
from app.persistence.postgres_memory import PostgresConversationMemoryStore
from app.persistence.postgres_runtime import (
    PostgresAgentStateStore,
    SessionStateConflictError,
)
from app.persistence.postgres_schema import (
    AGENT_STATE_SCHEMA,
    PostgresAgentStateSchemaManager,
)
from app.persistence.postgres_submission import PostgresMockApprovalSubmitter
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftGenerationResult, DraftStatus, DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker
from app.tools.mock_approval_submission import SubmissionConflictError
from app.tools.submission_models import SubmissionAuditEvent

pytestmark = pytest.mark.postgres_integration

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_SESSION_ID = "postgres-integration-session"
_NOW = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
_CONFIRMED_AT = datetime(2026, 8, 29, 9, 5, tzinfo=UTC)
_USER_CONTEXT = DraftUserContext(
    employee_id="INTEGRATION-EMP-001",
    employee_name="集成测试用户",
    department="研发部",
    roles=("EMPLOYEE",),
    region="中国大陆",
    identity_source="trusted_integration_context",
)
_COMPLETE_PURCHASE = (
    "帮我生成采购申请草稿，采购3台27英寸办公显示器，每台2000元，"
    "采购目的为给新员工配置办公设备，采购类别为IT设备，规格为27英寸2K，"
    "预算编号RD-2026，交付日期2026-09-15，使用地点苏州办公室，"
    "推荐供应商为苏州科技有限公司，推荐理由为历史合作交付稳定，普通采购，"
    "已准备技术需求说明、信息技术评审意见、产品规格说明和2家供应商报价。"
)


def _test_dsn() -> str:
    dsn = os.getenv("AGENT_POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AGENT_POSTGRES_TEST_DSN is not configured")
    database = str(conninfo_to_dict(dsn).get("dbname", ""))
    if not database.endswith("_test"):
        pytest.fail("AGENT_POSTGRES_TEST_DSN database name must end with _test")
    return dsn


@pytest.fixture(scope="session")
def postgres_test_dsn() -> str:
    dsn = _test_dsn()
    status = PostgresAgentStateSchemaManager.from_dsn(
        dsn,
        connect_timeout_seconds=5.0,
    ).setup()
    assert status.ready
    return dsn


@pytest_asyncio.fixture
async def postgres_pool(postgres_test_dsn: str):
    pool = AsyncConnectionPool(
        conninfo=postgres_test_dsn,
        min_size=1,
        max_size=12,
        timeout=5.0,
        open=False,
    )
    await pool.open()
    await pool.wait(timeout=10.0)
    async with pool.connection() as connection:
        await connection.execute(
            f"""
            TRUNCATE TABLE
                {AGENT_STATE_SCHEMA}.submission_audit_records,
                {AGENT_STATE_SCHEMA}.approval_submissions,
                {AGENT_STATE_SCHEMA}.conversation_messages,
                {AGENT_STATE_SCHEMA}.application_draft_snapshots,
                {AGENT_STATE_SCHEMA}.agent_sessions
            """
        )
    try:
        yield pool
    finally:
        await pool.close()


def _session(
    *,
    turn_number: int,
    phase: AgentSessionPhase,
    result: DraftGenerationResult | None = None,
    pending_confirmation: bool = False,
) -> AgentSessionInfo:
    draft = result.draft if result is not None else None
    return AgentSessionInfo(
        session_id=_SESSION_ID,
        turn_number=turn_number,
        phase=phase,
        active_draft_id=draft.draft_id if draft is not None else None,
        draft_revision=draft.revision if draft is not None else None,
        pending_confirmation=pending_confirmation,
        checkpoint_backend="sqlite",
        survives_process_restart=True,
    )


async def _waiting_draft() -> DraftGenerationResult:
    generator = ApplicationDraftGenerator.from_policy_directory(
        _POLICY_DIRECTORY,
        material_checker=RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY),
        approval_checker=ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY),
        user_context=_USER_CONTEXT,
        clock=lambda: _NOW,
    )
    answer = await generator.generate(_COMPLETE_PURCHASE, session_id=_SESSION_ID)
    assert answer.result.draft is not None
    draft = replace(
        answer.result.draft,
        audit_metadata=replace(answer.result.draft.audit_metadata, persisted=True),
    )
    assert draft.status is DraftStatus.WAITING_FOR_CONFIRMATION
    return replace(answer.result, draft=draft)


def _confirmed_draft(waiting: DraftGenerationResult):
    assert waiting.draft is not None
    return replace(
        waiting.draft,
        status=DraftStatus.CONFIRMED,
        confirmation_required=False,
        user_confirmed=True,
        confirmed_at=_CONFIRMED_AT,
    )


@pytest.mark.asyncio
async def test_schema_pool_restart_and_repository_round_trip(
    postgres_test_dsn, postgres_pool
) -> None:
    store = PostgresAgentStateStore(pool=postgres_pool, clock=lambda: _NOW)
    await store.ping()
    await store.save_route_state(
        _session(turn_number=1, phase=AgentSessionPhase.COLLECTING_INFORMATION),
        None,
    )

    replacement_pool = AsyncConnectionPool(
        conninfo=postgres_test_dsn,
        min_size=1,
        max_size=2,
        timeout=5.0,
        open=False,
    )
    await replacement_pool.open()
    await replacement_pool.wait(timeout=10.0)
    try:
        restored = await PostgresAgentStateStore(pool=replacement_pool).get_session(_SESSION_ID)
    finally:
        await replacement_pool.close()

    assert restored is not None
    assert restored.turn_number == 1
    assert restored.phase is AgentSessionPhase.COLLECTING_INFORMATION


@pytest.mark.asyncio
async def test_concurrent_session_heads_allow_one_winner(postgres_pool) -> None:
    store = PostgresAgentStateStore(pool=postgres_pool, clock=lambda: _NOW)
    await store.save_route_state(
        _session(turn_number=1, phase=AgentSessionPhase.COLLECTING_INFORMATION),
        None,
    )

    outcomes = await asyncio.gather(
        store.save_route_state(
            _session(turn_number=2, phase=AgentSessionPhase.CONFIRMED),
            None,
        ),
        store.save_route_state(
            _session(turn_number=2, phase=AgentSessionPhase.CANCELLED),
            None,
        ),
        return_exceptions=True,
    )

    assert sum(outcome is None for outcome in outcomes) == 1
    conflicts = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    assert len(conflicts) == 1
    assert isinstance(conflicts[0], SessionStateConflictError)
    stored = await store.get_session(_SESSION_ID)
    assert stored is not None
    assert stored.turn_number == 2
    assert stored.phase in {AgentSessionPhase.CONFIRMED, AgentSessionPhase.CANCELLED}


@pytest.mark.asyncio
async def test_draft_lifecycle_and_soft_delete_are_transactional(postgres_pool) -> None:
    waiting = await _waiting_draft()
    store = PostgresAgentStateStore(pool=postgres_pool, clock=lambda: _NOW)
    await store.save_route_state(
        _session(
            turn_number=1,
            phase=AgentSessionPhase.AWAITING_CONFIRMATION,
            result=waiting,
            pending_confirmation=True,
        ),
        waiting,
    )
    assert waiting.draft is not None
    assert await store.get_draft(waiting.draft.draft_id) is not None

    await store.delete_session(_SESSION_ID)

    assert await store.get_session(_SESSION_ID) is None
    assert await store.get_draft(waiting.draft.draft_id) is None
    assert await store.list_draft_revisions(waiting.draft.draft_id) == ()


@pytest.mark.asyncio
async def test_concurrent_conversation_appends_allocate_complete_ordered_turns(
    postgres_pool,
) -> None:
    await PostgresAgentStateStore(pool=postgres_pool).save_route_state(
        _session(turn_number=0, phase=AgentSessionPhase.IDLE),
        None,
    )
    memory = PostgresConversationMemoryStore(pool=postgres_pool, max_stored_turns=20)

    await asyncio.gather(
        *(
            memory.append_turn(
                _SESSION_ID,
                user_message=f"问题 {index}",
                assistant_message=f"回答 {index}",
            )
            for index in range(1, 11)
        )
    )
    snapshot = await memory.get_snapshot(_SESSION_ID, limit=100)

    assert snapshot.total_message_count == 20
    assert len(snapshot.messages) == 20
    assert [message.turn_number for message in snapshot.messages] == [
        turn for turn in range(1, 11) for _ in range(2)
    ]
    for index in range(0, len(snapshot.messages), 2):
        assert snapshot.messages[index].role.value == "user"
        assert snapshot.messages[index + 1].role.value == "assistant"


@pytest.mark.asyncio
async def test_concurrent_same_key_submission_has_one_receipt_and_replay_audits(
    postgres_pool,
) -> None:
    waiting = await _waiting_draft()
    draft = _confirmed_draft(waiting)
    submitter = PostgresMockApprovalSubmitter(pool=postgres_pool)

    results = await asyncio.gather(
        *(
            submitter.submit(
                draft,
                confirmation_text="提交审批",
                user_context=_USER_CONTEXT,
                session_id=_SESSION_ID,
                request_id=f"SAME-KEY-{index}",
                submission_idempotency_key="integration-same-key",
            )
            for index in range(8)
        )
    )

    assert len({result.submission_result.submission_id for result in results}) == 1
    assert sum(not result.duplicate_submission for result in results) == 1
    audits = await submitter.list_audit_records(draft_id=draft.draft_id)
    assert len(audits) == 8
    assert sum(audit.event is SubmissionAuditEvent.SUBMITTED for audit in audits) == 1
    assert sum(audit.event is SubmissionAuditEvent.IDEMPOTENT_REPLAY for audit in audits) == 7


@pytest.mark.asyncio
async def test_concurrent_different_keys_for_one_draft_fail_closed(postgres_pool) -> None:
    waiting = await _waiting_draft()
    draft = _confirmed_draft(waiting)
    submitter = PostgresMockApprovalSubmitter(pool=postgres_pool)

    outcomes = await asyncio.gather(
        *(
            submitter.submit(
                draft,
                confirmation_text="提交审批",
                user_context=_USER_CONTEXT,
                session_id=_SESSION_ID,
                request_id=f"DIFFERENT-KEY-{index}",
                submission_idempotency_key=f"integration-different-key-{index}",
            )
            for index in range(2)
        ),
        return_exceptions=True,
    )

    successes = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
    conflicts = [outcome for outcome in outcomes if isinstance(outcome, SubmissionConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    audits = await submitter.list_audit_records(draft_id=draft.draft_id)
    assert len(audits) == 1
    assert audits[0].event is SubmissionAuditEvent.SUBMITTED
