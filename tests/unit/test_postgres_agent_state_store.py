from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from psycopg.types.json import Jsonb

from app.agent.workflow_models import AgentSessionInfo, AgentSessionPhase
from app.persistence.postgres_runtime import (
    DraftRevisionConflictError,
    PostgresAgentStateStore,
    SessionDeletedError,
    SessionStateConflictError,
)
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftGenerationResult, DraftStatus, DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
_CONFIRMED_AT = datetime(2026, 8, 28, 12, 5, tzinfo=UTC)
_DELETED_AT = datetime(2026, 8, 28, 12, 10, tzinfo=UTC)
_SESSION_ID = "postgres-session-draft"
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
        self.committed = False
        self.rolled_back = False

    @asynccontextmanager
    async def connection(self, timeout: float | None = None) -> AsyncIterator[_Connection]:
        del timeout
        try:
            yield self.connection_instance
        except BaseException:
            self.rolled_back = True
            raise
        else:
            self.committed = True


async def _waiting_draft() -> DraftGenerationResult:
    material_checker = RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)
    approval_checker = ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)
    generator = ApplicationDraftGenerator.from_policy_directory(
        _POLICY_DIRECTORY,
        material_checker=material_checker,
        approval_checker=approval_checker,
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


def _confirmed(waiting: DraftGenerationResult) -> DraftGenerationResult:
    assert waiting.draft is not None
    return replace(
        waiting,
        draft=replace(
            waiting.draft,
            status=DraftStatus.CONFIRMED,
            confirmation_required=False,
            user_confirmed=True,
            confirmed_at=_CONFIRMED_AT,
        ),
        clarification_question=None,
    )


def _session(
    result: DraftGenerationResult,
    *,
    turn_number: int,
    phase: AgentSessionPhase,
    pending_confirmation: bool,
) -> AgentSessionInfo:
    assert result.draft is not None
    return AgentSessionInfo(
        session_id=_SESSION_ID,
        turn_number=turn_number,
        phase=phase,
        active_draft_id=result.draft.draft_id,
        draft_revision=result.draft.revision,
        pending_confirmation=pending_confirmation,
        checkpoint_backend="sqlite",
        survives_process_restart=True,
    )


def _json_payload(result: DraftGenerationResult) -> dict[str, Any]:
    from pydantic import TypeAdapter

    payload = TypeAdapter(DraftGenerationResult).dump_python(
        result,
        mode="json",
        warnings=False,
    )
    assert isinstance(payload, dict)
    return payload


@pytest.mark.asyncio
async def test_inserts_session_and_draft_in_one_pool_transaction() -> None:
    draft = await _waiting_draft()
    assert draft.draft is not None
    pool = _Pool(
        (
            _ExpectedExecution("INSERT INTO agent_runtime.agent_sessions", one=(1,)),
            _ExpectedExecution(
                "INSERT INTO agent_runtime.application_draft_snapshots",
                one=(draft.draft.draft_id,),
            ),
        )
    )
    store = PostgresAgentStateStore(pool=pool, clock=lambda: _NOW)

    await store.save_route_state(
        _session(
            draft,
            turn_number=1,
            phase=AgentSessionPhase.AWAITING_CONFIRMATION,
            pending_confirmation=True,
        ),
        draft,
    )

    assert pool.committed is True
    assert pool.rolled_back is False
    session_sql, _ = pool.connection_instance.executed[0]
    assert "ON CONFLICT (session_id) DO NOTHING" in session_sql
    _, draft_params = pool.connection_instance.executed[1]
    assert isinstance(draft_params[4], Jsonb)
    assert draft_params[4].obj["draft"]["draft_id"] == draft.draft.draft_id


@pytest.mark.asyncio
async def test_rejects_a_stale_session_turn_before_writing_the_draft() -> None:
    draft = await _waiting_draft()
    assert draft.draft is not None
    pool = _Pool(
        (
            _ExpectedExecution("INSERT INTO agent_runtime.agent_sessions"),
            _ExpectedExecution(
                "FROM agent_runtime.agent_sessions WHERE session_id = %s FOR UPDATE",
                one=(
                    3,
                    AgentSessionPhase.CONFIRMED.value,
                    draft.draft.draft_id,
                    draft.draft.revision,
                    False,
                    "sqlite",
                    7,
                    None,
                ),
            ),
        )
    )
    store = PostgresAgentStateStore(pool=pool, clock=lambda: _NOW)

    with pytest.raises(SessionStateConflictError, match="stale session turn"):
        await store.save_route_state(
            _session(
                draft,
                turn_number=2,
                phase=AgentSessionPhase.AWAITING_CONFIRMATION,
                pending_confirmation=True,
            ),
            draft,
        )

    assert pool.rolled_back is True
    assert len(pool.connection_instance.executed) == 2


@pytest.mark.asyncio
async def test_rejects_divergent_state_for_the_same_turn() -> None:
    draft = await _waiting_draft()
    assert draft.draft is not None
    pool = _Pool(
        (
            _ExpectedExecution("INSERT INTO agent_runtime.agent_sessions"),
            _ExpectedExecution(
                "FROM agent_runtime.agent_sessions WHERE session_id = %s FOR UPDATE",
                one=(
                    2,
                    AgentSessionPhase.CONFIRMED.value,
                    draft.draft.draft_id,
                    draft.draft.revision,
                    False,
                    "sqlite",
                    4,
                    None,
                ),
            ),
        )
    )

    with pytest.raises(SessionStateConflictError, match="same session turn"):
        await PostgresAgentStateStore(pool=pool).save_route_state(
            _session(
                draft,
                turn_number=2,
                phase=AgentSessionPhase.AWAITING_CONFIRMATION,
                pending_confirmation=True,
            ),
            draft,
        )

    assert pool.rolled_back is True


@pytest.mark.asyncio
async def test_retries_identical_session_and_draft_state_idempotently() -> None:
    draft = await _waiting_draft()
    assert draft.draft is not None
    session = _session(
        draft,
        turn_number=1,
        phase=AgentSessionPhase.AWAITING_CONFIRMATION,
        pending_confirmation=True,
    )
    pool = _Pool(
        (
            _ExpectedExecution("INSERT INTO agent_runtime.agent_sessions"),
            _ExpectedExecution(
                "FROM agent_runtime.agent_sessions WHERE session_id = %s FOR UPDATE",
                one=(
                    session.turn_number,
                    session.phase.value,
                    session.active_draft_id,
                    session.draft_revision,
                    session.pending_confirmation,
                    session.checkpoint_backend,
                    1,
                    None,
                ),
            ),
            _ExpectedExecution("INSERT INTO agent_runtime.application_draft_snapshots"),
            _ExpectedExecution(
                "FROM agent_runtime.application_draft_snapshots",
                one=(
                    session.session_id,
                    draft.draft.status.value,
                    _json_payload(draft),
                ),
            ),
        )
    )

    await PostgresAgentStateStore(pool=pool).save_route_state(session, draft)

    assert pool.committed is True
    assert len(pool.connection_instance.executed) == 4


@pytest.mark.asyncio
async def test_allows_guarded_lifecycle_update_inside_the_same_content_revision() -> None:
    waiting = await _waiting_draft()
    confirmed = _confirmed(waiting)
    assert waiting.draft is not None
    session = _session(
        confirmed,
        turn_number=2,
        phase=AgentSessionPhase.CONFIRMED,
        pending_confirmation=False,
    )
    pool = _Pool(
        (
            _ExpectedExecution("INSERT INTO agent_runtime.agent_sessions"),
            _ExpectedExecution(
                "FROM agent_runtime.agent_sessions WHERE session_id = %s FOR UPDATE",
                one=(
                    1,
                    AgentSessionPhase.AWAITING_CONFIRMATION.value,
                    session.active_draft_id,
                    session.draft_revision,
                    True,
                    "sqlite",
                    7,
                    None,
                ),
            ),
            _ExpectedExecution("UPDATE agent_runtime.agent_sessions", one=(8,)),
            _ExpectedExecution("INSERT INTO agent_runtime.application_draft_snapshots"),
            _ExpectedExecution(
                "FROM agent_runtime.application_draft_snapshots",
                one=(
                    session.session_id,
                    waiting.draft.status.value,
                    _json_payload(waiting),
                ),
            ),
            _ExpectedExecution(
                "UPDATE agent_runtime.application_draft_snapshots",
                one=(waiting.draft.draft_id,),
            ),
        )
    )

    await PostgresAgentStateStore(pool=pool, clock=lambda: _NOW).save_route_state(
        session,
        confirmed,
    )

    assert pool.committed is True
    session_update_sql, session_update_params = pool.connection_instance.executed[2]
    assert "state_version = state_version + 1" in session_update_sql
    assert "AND state_version = %s" in session_update_sql
    assert session_update_params[-1] == 7
    draft_update_sql, draft_update_params = pool.connection_instance.executed[5]
    assert "AND status = %s" in draft_update_sql
    assert draft_update_params[-1] == DraftStatus.WAITING_FOR_CONFIRMATION.value


@pytest.mark.asyncio
async def test_rejects_business_content_replacement_inside_existing_revision() -> None:
    waiting = await _waiting_draft()
    confirmed = _confirmed(waiting)
    assert waiting.draft is not None
    assert confirmed.draft is not None
    changed = replace(
        confirmed,
        draft=replace(confirmed.draft, title="被篡改的同版本标题"),
    )
    session = _session(
        changed,
        turn_number=2,
        phase=AgentSessionPhase.CONFIRMED,
        pending_confirmation=False,
    )
    pool = _Pool(
        (
            _ExpectedExecution("INSERT INTO agent_runtime.agent_sessions"),
            _ExpectedExecution(
                "FROM agent_runtime.agent_sessions WHERE session_id = %s FOR UPDATE",
                one=(
                    1,
                    AgentSessionPhase.AWAITING_CONFIRMATION.value,
                    session.active_draft_id,
                    session.draft_revision,
                    True,
                    "sqlite",
                    2,
                    None,
                ),
            ),
            _ExpectedExecution("UPDATE agent_runtime.agent_sessions", one=(3,)),
            _ExpectedExecution("INSERT INTO agent_runtime.application_draft_snapshots"),
            _ExpectedExecution(
                "FROM agent_runtime.application_draft_snapshots",
                one=(
                    session.session_id,
                    waiting.draft.status.value,
                    _json_payload(waiting),
                ),
            ),
        )
    )

    with pytest.raises(DraftRevisionConflictError, match="business content"):
        await PostgresAgentStateStore(pool=pool).save_route_state(session, changed)

    assert pool.rolled_back is True
    assert len(pool.connection_instance.executed) == 5


@pytest.mark.asyncio
async def test_tombstoned_session_cannot_be_revived() -> None:
    draft = await _waiting_draft()
    assert draft.draft is not None
    session = _session(
        draft,
        turn_number=2,
        phase=AgentSessionPhase.AWAITING_CONFIRMATION,
        pending_confirmation=True,
    )
    pool = _Pool(
        (
            _ExpectedExecution("INSERT INTO agent_runtime.agent_sessions"),
            _ExpectedExecution(
                "FROM agent_runtime.agent_sessions WHERE session_id = %s FOR UPDATE",
                one=(
                    1,
                    session.phase.value,
                    session.active_draft_id,
                    session.draft_revision,
                    True,
                    "sqlite",
                    3,
                    _DELETED_AT,
                ),
            ),
        )
    )

    with pytest.raises(SessionDeletedError, match="cannot be revived"):
        await PostgresAgentStateStore(pool=pool).save_route_state(session, draft)

    assert pool.rolled_back is True


@pytest.mark.asyncio
async def test_delete_session_uses_tombstone_and_removes_only_mutable_drafts() -> None:
    pool = _Pool(
        (
            _ExpectedExecution(
                "SELECT state_version, deleted_at FROM agent_runtime.agent_sessions",
                one=(5, None),
            ),
            _ExpectedExecution("UPDATE agent_runtime.agent_sessions", one=(6,)),
            _ExpectedExecution("DELETE FROM agent_runtime.application_draft_snapshots"),
        )
    )

    await PostgresAgentStateStore(pool=pool, clock=lambda: _DELETED_AT).delete_session(_SESSION_ID)

    all_sql = "\n".join(query for query, _ in pool.connection_instance.executed)
    assert "SET deleted_at = %s" in all_sql
    assert "DELETE FROM agent_runtime.agent_sessions" not in all_sql
    assert "approval_submissions" not in all_sql
    assert "submission_audit_records" not in all_sql
    assert pool.committed is True


@pytest.mark.asyncio
async def test_reads_live_session_latest_draft_and_ordered_revisions() -> None:
    draft = await _waiting_draft()
    assert draft.draft is not None
    pool = _Pool(
        (
            _ExpectedExecution(
                "FROM agent_runtime.agent_sessions WHERE session_id = %s AND deleted_at IS NULL",
                one=(
                    _SESSION_ID,
                    1,
                    AgentSessionPhase.AWAITING_CONFIRMATION.value,
                    draft.draft.draft_id,
                    draft.draft.revision,
                    True,
                    "sqlite",
                    _NOW,
                ),
            ),
            _ExpectedExecution(
                "ORDER BY draft.revision DESC LIMIT 1",
                one=(_json_payload(draft),),
            ),
            _ExpectedExecution(
                "ORDER BY draft.revision",
                rows=((1,), (2,), (4,)),
            ),
        )
    )
    store = PostgresAgentStateStore(pool=pool)

    stored_session = await store.get_session(_SESSION_ID)
    stored_draft = await store.get_draft(draft.draft.draft_id)
    revisions = await store.list_draft_revisions(draft.draft.draft_id)

    assert stored_session is not None
    assert stored_session.phase is AgentSessionPhase.AWAITING_CONFIRMATION
    assert stored_draft is not None
    assert _json_payload(stored_draft) == _json_payload(draft)
    assert revisions == (1, 2, 4)


@pytest.mark.asyncio
async def test_ping_requires_exact_schema_version() -> None:
    ready_pool = _Pool(
        (
            _ExpectedExecution("SELECT 1", one=(1,)),
            _ExpectedExecution(
                "SELECT COALESCE(MAX(version), 0)",
                one=(1,),
            ),
        )
    )
    await PostgresAgentStateStore(pool=ready_pool).ping()

    drifted_pool = _Pool(
        (
            _ExpectedExecution("SELECT 1", one=(1,)),
            _ExpectedExecution(
                "SELECT COALESCE(MAX(version), 0)",
                one=(2,),
            ),
        )
    )
    with pytest.raises(RuntimeError, match="does not match"):
        await PostgresAgentStateStore(pool=drifted_pool).ping()


def test_postgres_state_store_reports_shared_durable_backend() -> None:
    assert PostgresAgentStateStore.backend_name == "postgresql"
    assert PostgresAgentStateStore.survives_process_restart is True
