from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import (
    AgentResponseStatus,
    AgentRouter,
    AgentSessionPhase,
)
from app.persistence import (
    SQLiteAgentStateStore,
    SQLiteCheckpointSaver,
    SQLiteMockApprovalSubmitter,
)
from app.rag.policy_answer_service import PolicyAnswer
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import (
    DraftStatus,
    DraftUserContext,
)
from app.tools.material_check import RequiredMaterialsChecker
from app.tools.submission_models import SubmissionAuditEvent

_POLICY_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "policies"
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
    "预算编号RD-2026，交付日期2026-08-15，使用地点苏州办公室，"
    "推荐供应商为苏州科技有限公司，推荐理由为历史合作交付稳定，普通采购，"
    "已准备技术需求说明、信息技术评审意见、产品规格说明和2家供应商报价。"
)
_PURCHASE_DETAILS = _COMPLETE_PURCHASE.replace(
    "帮我生成采购申请草稿，",
    "",
)


class DeterministicIntentClassifier:
    async def classify(
        self,
        user_input: str,
    ) -> IntentClassification:
        return IntentClassification(
            intent=(
                IntentType.DRAFT_GENERATION
                if "生成" in user_input and "草稿" in user_input
                else IntentType.UNKNOWN
            ),
            confidence=1.0,
            reason="Day 15 SQLite 持久化测试分类。",
        )


class StubPolicyAnswerService:
    async def answer(self, question: str) -> PolicyAnswer:
        return PolicyAnswer(
            question=question,
            answer="本测试不验证制度问答。",
            citations=(),
        )


def _build_router(database_path: Path) -> AgentRouter:
    material_checker = RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)
    approval_checker = ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)
    return AgentRouter(
        intent_classifier=DeterministicIntentClassifier(),
        policy_answer_service=StubPolicyAnswerService(),
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=ApplicationDraftGenerator.from_policy_directory(
            _POLICY_DIRECTORY,
            material_checker=material_checker,
            approval_checker=approval_checker,
            user_context=_USER_CONTEXT,
        ),
        submission_service=SQLiteMockApprovalSubmitter(database_path),
        checkpointer=SQLiteCheckpointSaver(database_path),
        state_persister=SQLiteAgentStateStore(database_path),
    )


def _draft(result):
    assert result.application_draft is not None
    assert result.application_draft.draft is not None
    return result.application_draft.draft


async def _confirmed(
    database_path: Path,
    session_id: str,
):
    created = await _build_router(database_path).route(
        _COMPLETE_PURCHASE,
        session_id=session_id,
    )
    assert created.status is AgentResponseStatus.AWAITING_CONFIRMATION
    return await _build_router(database_path).route(
        "确认草稿",
        session_id=session_id,
    )


@pytest.mark.asyncio
async def test_incomplete_draft_continues_after_process_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "conversation.db"
    session_id = "sqlite-collect-after-restart"

    first = await _build_router(database_path).route(
        "帮我生成采购申请草稿。",
        session_id=session_id,
    )
    second = await _build_router(database_path).route(
        _PURCHASE_DETAILS,
        session_id=session_id,
    )

    assert first.status is AgentResponseStatus.NEEDS_CLARIFICATION
    assert first.session is not None
    assert first.session.phase is AgentSessionPhase.COLLECTING_INFORMATION
    assert second.status is AgentResponseStatus.AWAITING_CONFIRMATION
    assert _draft(second).draft_id == _draft(first).draft_id
    assert _draft(second).revision == 2
    assert _draft(second).audit_metadata.persisted is True
    assert second.session is not None
    assert second.session.checkpoint_backend == "sqlite"
    assert second.session.survives_process_restart is True


@pytest.mark.asyncio
async def test_confirmation_interrupt_resumes_after_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "confirmation.db"
    session_id = "sqlite-confirm-after-restart"
    created = await _build_router(database_path).route(
        _COMPLETE_PURCHASE,
        session_id=session_id,
    )

    confirmed = await _build_router(database_path).route(
        "确认草稿",
        session_id=session_id,
    )

    assert created.session is not None
    assert created.session.pending_confirmation is True
    assert confirmed.status is AgentResponseStatus.CONFIRMED
    assert confirmed.session is not None
    assert confirmed.session.turn_number == 2
    assert confirmed.session.pending_confirmation is False
    assert _draft(confirmed).status is DraftStatus.CONFIRMED
    assert _draft(confirmed).user_confirmed is True


@pytest.mark.asyncio
async def test_submission_and_idempotent_replay_survive_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "submission.db"
    session_id = "sqlite-submit-after-restart"
    await _confirmed(database_path, session_id)

    submitted = await _build_router(database_path).route(
        "提交审批",
        session_id=session_id,
    )
    replay = await _build_router(database_path).route(
        "提交审批",
        session_id=session_id,
    )

    assert submitted.submission is not None
    assert replay.submission is not None
    assert submitted.submission.storage_backend == "sqlite"
    assert submitted.submission.survives_process_restart is True
    assert replay.submission.duplicate_submission is True
    assert replay.submission.submission_result.submission_id == (
        submitted.submission.submission_result.submission_id
    )
    assert replay.submission.audit_record.event is (SubmissionAuditEvent.IDEMPOTENT_REPLAY)
    records = await SQLiteMockApprovalSubmitter(database_path).list_audit_records()
    assert [record.event for record in records] == [
        SubmissionAuditEvent.SUBMITTED,
        SubmissionAuditEvent.IDEMPOTENT_REPLAY,
    ]


@pytest.mark.asyncio
async def test_persists_session_and_versioned_draft_snapshots(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "projection.db"
    session_id = "sqlite-projection"
    store = SQLiteAgentStateStore(database_path)
    first = await _build_router(database_path).route(
        "帮我生成采购申请草稿。",
        session_id=session_id,
    )
    await _build_router(database_path).route(
        _PURCHASE_DETAILS,
        session_id=session_id,
    )
    confirmed = await _build_router(database_path).route(
        "确认草稿",
        session_id=session_id,
    )
    draft = _draft(confirmed)

    stored_session = await store.get_session(session_id)
    stored_draft = await store.get_draft(draft.draft_id)
    revisions = await store.list_draft_revisions(draft.draft_id)

    assert _draft(first).revision == 1
    assert stored_session is not None
    assert stored_session.turn_number == 3
    assert stored_session.phase is AgentSessionPhase.CONFIRMED
    assert stored_session.active_draft_id == draft.draft_id
    assert stored_draft is not None
    assert stored_draft.draft is not None
    assert stored_draft.draft.status is DraftStatus.CONFIRMED
    assert stored_draft.draft.audit_metadata.persisted is True
    assert revisions == (1, 2)


@pytest.mark.asyncio
async def test_submitted_draft_stays_immutable_after_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "immutable.db"
    session_id = "sqlite-submitted-immutable"
    await _confirmed(database_path, session_id)
    submitted = await _build_router(database_path).route(
        "提交审批",
        session_id=session_id,
    )

    modified = await _build_router(database_path).route(
        "把预计单价改为2200元",
        session_id=session_id,
    )

    assert modified.status is AgentResponseStatus.NEEDS_CLARIFICATION
    assert _draft(modified).status is DraftStatus.SUBMITTED
    assert _draft(modified).submission_id == _draft(submitted).submission_id


@pytest.mark.asyncio
async def test_concurrent_submitters_share_database_idempotency(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "concurrent.db"
    session_id = "sqlite-cross-instance-concurrent"
    confirmed = await _confirmed(database_path, session_id)
    draft = _draft(confirmed)
    first_service = SQLiteMockApprovalSubmitter(database_path)
    second_service = SQLiteMockApprovalSubmitter(database_path)

    first, second = await asyncio.gather(
        first_service.submit(
            draft,
            confirmation_text="提交审批",
            user_context=_USER_CONTEXT,
            session_id=session_id,
            request_id="SQLITE-CONCURRENT-001",
            submission_idempotency_key="sqlite-shared-key-001",
        ),
        second_service.submit(
            draft,
            confirmation_text="提交审批",
            user_context=_USER_CONTEXT,
            session_id=session_id,
            request_id="SQLITE-CONCURRENT-002",
            submission_idempotency_key="sqlite-shared-key-001",
        ),
    )

    assert first.submission_result.submission_id == (second.submission_result.submission_id)
    assert sum(result.duplicate_submission for result in (first, second)) == 1
    records = await SQLiteMockApprovalSubmitter(database_path).list_audit_records(
        draft_id=draft.draft_id
    )
    assert len(records) == 2


@pytest.mark.asyncio
async def test_clear_session_keeps_immutable_submission_audit(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "clear.db"
    session_id = "sqlite-clear-session"
    confirmed = await _confirmed(database_path, session_id)
    draft_id = _draft(confirmed).draft_id
    router = _build_router(database_path)
    await router.route("提交审批", session_id=session_id)

    await router.clear_session(session_id)

    state_store = SQLiteAgentStateStore(database_path)
    submitter = SQLiteMockApprovalSubmitter(database_path)
    assert await state_store.get_session(session_id) is None
    assert await state_store.get_draft(draft_id) is None
    assert len(await submitter.list_audit_records()) == 1
    assert await submitter.get_submission(draft_id=draft_id) is not None

    missing = await _build_router(database_path).route(
        "提交审批",
        session_id=session_id,
    )
    assert missing.status is AgentResponseStatus.NEEDS_CLARIFICATION
    assert missing.application_draft is None
