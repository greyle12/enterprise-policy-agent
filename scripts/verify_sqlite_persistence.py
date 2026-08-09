from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

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
from app.tools.draft_models import DraftStatus, DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker
from app.tools.submission_models import SubmissionAuditEvent

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_PURCHASE_DETAILS = (
    "采购3台27英寸办公显示器，每台2000元，"
    "采购目的为给新员工配置办公设备，采购类别为IT设备，规格为27英寸2K，"
    "预算编号RD-2026，交付日期2026-08-15，使用地点苏州办公室，"
    "推荐供应商为苏州科技有限公司，推荐理由为历史合作交付稳定，普通采购，"
    "已准备技术需求说明、信息技术评审意见、产品规格说明和2家供应商报价。"
)


class DeterministicIntentClassifier:
    """Day 15 本地验收不调用外部 LLM。"""

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
            reason="Day 15 SQLite 持久化验收分类。",
        )


class StubPolicyAnswerService:
    async def answer(self, question: str) -> PolicyAnswer:
        return PolicyAnswer(
            question=question,
            answer="本脚本不验证制度问答内容。",
            citations=(),
        )


def _build_router(database_path: Path) -> AgentRouter:
    material_checker = RequiredMaterialsChecker.from_policy_directory(
        _POLICY_DIRECTORY
    )
    approval_checker = ApprovalRuleChecker.from_policy_directory(
        _POLICY_DIRECTORY
    )
    return AgentRouter(
        intent_classifier=DeterministicIntentClassifier(),
        policy_answer_service=StubPolicyAnswerService(),
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=ApplicationDraftGenerator.from_policy_directory(
            _POLICY_DIRECTORY,
            material_checker=material_checker,
            approval_checker=approval_checker,
            user_context=DraftUserContext(
                employee_id="DEMO-EMP-001",
                employee_name="演示用户",
                department="演示部门",
                roles=("EMPLOYEE",),
                region="中国大陆",
                identity_source="trusted_demo_context",
            ),
        ),
        submission_service=SQLiteMockApprovalSubmitter(database_path),
        checkpointer=SQLiteCheckpointSaver(database_path),
        state_persister=SQLiteAgentStateStore(database_path),
    )


def _draft(result):
    if (
        result.application_draft is None
        or result.application_draft.draft is None
    ):
        raise RuntimeError("verification expected an application draft")
    return result.application_draft.draft


def _field_value(draft, field_name: str):
    return next(
        field.value
        for field in draft.fields
        if field.field_name == field_name
    )


def _print_result(name: str, result, *, passed: bool) -> None:
    draft = (
        result.application_draft.draft
        if result.application_draft is not None
        else None
    )
    print(
        json.dumps(
            {
                "name": name,
                "status": result.status,
                "phase": (
                    result.session.phase
                    if result.session is not None
                    else None
                ),
                "turn_number": (
                    result.session.turn_number
                    if result.session is not None
                    else None
                ),
                "checkpoint_backend": (
                    result.session.checkpoint_backend
                    if result.session is not None
                    else None
                ),
                "survives_process_restart": (
                    result.session.survives_process_restart
                    if result.session is not None
                    else None
                ),
                "draft_id": (
                    draft.draft_id if draft is not None else None
                ),
                "revision": (
                    draft.revision if draft is not None else None
                ),
                "draft_status": (
                    draft.status if draft is not None else None
                ),
                "submission_id": (
                    result.submission.submission_result.submission_id
                    if result.submission is not None
                    else None
                ),
                "duplicate_submission": (
                    result.submission.duplicate_submission
                    if result.submission is not None
                    else None
                ),
                "passed": passed,
            },
            ensure_ascii=False,
        )
    )


def _print_projection(
    *,
    session,
    draft,
    revisions: tuple[int, ...],
    audit_events: list[SubmissionAuditEvent],
    passed: bool,
) -> None:
    print(
        json.dumps(
            {
                "name": "query_persisted_records",
                "phase": session.phase if session is not None else None,
                "draft_status": (
                    draft.draft.status
                    if draft is not None and draft.draft is not None
                    else None
                ),
                "revisions": revisions,
                "audit_events": audit_events,
                "passed": passed,
            },
            ensure_ascii=False,
        )
    )


async def _main() -> None:
    failures: list[str] = []
    with TemporaryDirectory(prefix="enterprise-agent-day15-") as directory:
        database_path = Path(directory) / "agent.db"
        session_id = "DAY15-SQLITE-VERIFY"

        first = await _build_router(database_path).route(
            "帮我生成采购申请草稿。",
            session_id=session_id,
        )
        first_draft = _draft(first)
        passed = (
            first.status is AgentResponseStatus.NEEDS_CLARIFICATION
            and first.session is not None
            and first.session.checkpoint_backend == "sqlite"
            and first.session.survives_process_restart
            and first_draft.audit_metadata.persisted
        )
        _print_result("persist_incomplete_draft", first, passed=passed)
        if not passed:
            failures.append("persist_incomplete_draft")

        completed = await _build_router(database_path).route(
            _PURCHASE_DETAILS,
            session_id=session_id,
        )
        passed = (
            completed.status
            is AgentResponseStatus.AWAITING_CONFIRMATION
            and _draft(completed).draft_id == first_draft.draft_id
            and _draft(completed).revision == 2
        )
        _print_result("resume_information_collection", completed, passed=passed)
        if not passed:
            failures.append("resume_information_collection")

        modified = await _build_router(database_path).route(
            "把预计单价改为2200元",
            session_id=session_id,
        )
        passed = (
            modified.status
            is AgentResponseStatus.AWAITING_CONFIRMATION
            and _draft(modified).revision == 3
            and _field_value(_draft(modified), "estimated_unit_price")
            == Decimal(2200)
            and _field_value(_draft(modified), "estimated_total_amount")
            == Decimal(6600)
        )
        _print_result("resume_draft_update", modified, passed=passed)
        if not passed:
            failures.append("resume_draft_update")

        confirmed = await _build_router(database_path).route(
            "确认草稿",
            session_id=session_id,
        )
        passed = (
            confirmed.status is AgentResponseStatus.CONFIRMED
            and _draft(confirmed).status is DraftStatus.CONFIRMED
            and not _draft(confirmed).submitted
        )
        _print_result("resume_human_confirmation", confirmed, passed=passed)
        if not passed:
            failures.append("resume_human_confirmation")

        submitted = await _build_router(database_path).route(
            "提交审批",
            session_id=session_id,
        )
        submitted_id = (
            submitted.submission.submission_result.submission_id
            if submitted.submission is not None
            else None
        )
        passed = (
            submitted.status is AgentResponseStatus.SUBMITTED
            and submitted.submission is not None
            and submitted.submission.storage_backend == "sqlite"
            and submitted.submission.survives_process_restart
            and not submitted.submission.duplicate_submission
        )
        _print_result("persist_mock_submission", submitted, passed=passed)
        if not passed:
            failures.append("persist_mock_submission")

        replay = await _build_router(database_path).route(
            "提交审批",
            session_id=session_id,
        )
        passed = (
            replay.submission is not None
            and replay.submission.duplicate_submission
            and replay.submission.submission_result.submission_id
            == submitted_id
            and replay.submission.audit_record.event
            is SubmissionAuditEvent.IDEMPOTENT_REPLAY
        )
        _print_result("restart_safe_idempotent_replay", replay, passed=passed)
        if not passed:
            failures.append("restart_safe_idempotent_replay")

        store = SQLiteAgentStateStore(database_path)
        submitter = SQLiteMockApprovalSubmitter(database_path)
        stored_session = await store.get_session(session_id)
        stored_draft = await store.get_draft(first_draft.draft_id)
        revisions = await store.list_draft_revisions(
            first_draft.draft_id
        )
        audit_events = [
            record.event
            for record in await submitter.list_audit_records(
                draft_id=first_draft.draft_id
            )
        ]
        passed = (
            stored_session is not None
            and stored_session.phase is AgentSessionPhase.SUBMITTED
            and stored_draft is not None
            and stored_draft.draft is not None
            and stored_draft.draft.status is DraftStatus.SUBMITTED
            and revisions == (1, 2, 3)
            and audit_events
            == [
                SubmissionAuditEvent.SUBMITTED,
                SubmissionAuditEvent.IDEMPOTENT_REPLAY,
            ]
        )
        _print_projection(
            session=stored_session,
            draft=stored_draft,
            revisions=revisions,
            audit_events=audit_events,
            passed=passed,
        )
        if not passed:
            failures.append("query_persisted_records")

        immutable = await _build_router(database_path).route(
            "把预计单价改为2400元",
            session_id=session_id,
        )
        passed = (
            immutable.status
            is AgentResponseStatus.NEEDS_CLARIFICATION
            and _draft(immutable).status is DraftStatus.SUBMITTED
            and _draft(immutable).submission_id == submitted_id
        )
        _print_result(
            "restart_safe_post_submit_immutability",
            immutable,
            passed=passed,
        )
        if not passed:
            failures.append("restart_safe_post_submit_immutability")

    if failures:
        raise RuntimeError(
            "Day 15 SQLite verification failed: "
            + " | ".join(failures)
        )


if __name__ == "__main__":
    asyncio.run(_main())
