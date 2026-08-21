from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import (
    AgentResponseStatus,
    AgentRouter,
    AgentSessionPhase,
)
from app.rag.policy_answer_service import PolicyAnswer
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftStatus, DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker
from app.tools.mock_approval_submission import MockApprovalSubmitter
from app.tools.submission_models import SubmissionAuditEvent

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_COMPLETE_PURCHASE = (
    "帮我生成采购申请草稿，采购3台27英寸办公显示器，每台2000元，"
    "采购目的为给新员工配置办公设备，采购类别为IT设备，规格为27英寸2K，"
    "预算编号RD-2026，交付日期2026-08-15，使用地点苏州办公室，"
    "推荐供应商为苏州科技有限公司，推荐理由为历史合作交付稳定，普通采购，"
    "已准备技术需求说明、信息技术评审意见、产品规格说明和2家供应商报价。"
)


class DeterministicIntentClassifier:
    """本地验收使用确定性分类，不调用外部 LLM。"""

    async def classify(
        self,
        user_input: str,
    ) -> IntentClassification:
        intent = (
            IntentType.DRAFT_GENERATION
            if "生成" in user_input and "草稿" in user_input
            else IntentType.UNKNOWN
        )
        return IntentClassification(
            intent=intent,
            confidence=1.0,
            reason="Day 14 模拟提交验收分类。",
        )


class StubPolicyAnswerService:
    async def answer(self, question: str) -> PolicyAnswer:
        return PolicyAnswer(
            question=question,
            answer="本脚本不验证制度问答内容。",
            citations=(),
        )


def _build_router() -> tuple[AgentRouter, MockApprovalSubmitter]:
    material_checker = RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)
    approval_checker = ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)
    submitter = MockApprovalSubmitter()
    return (
        AgentRouter(
            intent_classifier=DeterministicIntentClassifier(),
            policy_answer_service=StubPolicyAnswerService(),
            material_checker=material_checker,
            approval_checker=approval_checker,
            draft_generator=(
                ApplicationDraftGenerator.from_policy_directory(
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
                )
            ),
            submission_service=submitter,
        ),
        submitter,
    )


def _draft(result):
    if result.application_draft is None or result.application_draft.draft is None:
        raise RuntimeError("verification expected a draft")
    return result.application_draft.draft


def _print_result(name: str, result, *, passed: bool) -> None:
    draft = result.application_draft.draft if result.application_draft is not None else None
    submission = result.submission
    print(
        json.dumps(
            {
                "name": name,
                "status": result.status,
                "intent": result.classification.intent,
                "phase": (result.session.phase if result.session is not None else None),
                "draft_status": (draft.status if draft is not None else None),
                "submission_id": (
                    submission.submission_result.submission_id if submission is not None else None
                ),
                "duplicate_submission": (
                    submission.duplicate_submission if submission is not None else None
                ),
                "workflow_nodes": (
                    [step.node for step in result.workflow.steps]
                    if result.workflow is not None
                    else []
                ),
                "passed": passed,
            },
            ensure_ascii=False,
        )
    )


async def _main() -> None:
    router, submitter = _build_router()
    failures: list[str] = []
    session_id = "DAY14-SUBMISSION-VERIFY"

    created = await router.route(
        _COMPLETE_PURCHASE,
        session_id=session_id,
    )
    passed = (
        created.status is AgentResponseStatus.AWAITING_CONFIRMATION
        and _draft(created).status is DraftStatus.WAITING_FOR_CONFIRMATION
    )
    _print_result("create_confirmable_draft", created, passed=passed)
    if not passed:
        failures.append("create_confirmable_draft")

    premature = await router.route(
        "提交审批",
        session_id=session_id,
    )
    passed = (
        premature.status is AgentResponseStatus.AWAITING_CONFIRMATION
        and premature.session is not None
        and premature.session.pending_confirmation
        and not _draft(premature).submitted
        and not await submitter.list_audit_records()
    )
    _print_result("reject_unconfirmed_submission", premature, passed=passed)
    if not passed:
        failures.append("reject_unconfirmed_submission")

    confirmed = await router.route(
        "确认草稿",
        session_id=session_id,
    )
    passed = (
        confirmed.status is AgentResponseStatus.CONFIRMED
        and _draft(confirmed).user_confirmed
        and not _draft(confirmed).submitted
    )
    _print_result("confirm_without_submission", confirmed, passed=passed)
    if not passed:
        failures.append("confirm_without_submission")

    submitted = await router.route(
        "提交审批",
        session_id=session_id,
    )
    submitted_draft = _draft(submitted)
    passed = (
        submitted.status is AgentResponseStatus.SUBMITTED
        and submitted.session is not None
        and submitted.session.phase is AgentSessionPhase.SUBMITTED
        and submitted_draft.status is DraftStatus.SUBMITTED
        and submitted_draft.submitted
        and submitted.submission is not None
        and not submitted.submission.duplicate_submission
    )
    _print_result("submit_confirmed_draft", submitted, passed=passed)
    if not passed:
        failures.append("submit_confirmed_draft")

    replay = await router.route(
        "提交审批",
        session_id=session_id,
    )
    passed = (
        replay.submission is not None
        and submitted.submission is not None
        and replay.submission.duplicate_submission
        and replay.submission.submission_result.submission_id
        == submitted.submission.submission_result.submission_id
        and replay.submission.audit_record.event is SubmissionAuditEvent.IDEMPOTENT_REPLAY
    )
    _print_result("idempotent_replay", replay, passed=passed)
    if not passed:
        failures.append("idempotent_replay")

    immutable = await router.route(
        "把预计单价改为2200元",
        session_id=session_id,
    )
    passed = (
        immutable.status is AgentResponseStatus.NEEDS_CLARIFICATION
        and _draft(immutable).status is DraftStatus.SUBMITTED
        and _draft(immutable).submission_id == submitted_draft.submission_id
    )
    _print_result("reject_post_submission_update", immutable, passed=passed)
    if not passed:
        failures.append("reject_post_submission_update")

    question = await router.route(
        "现在可以提交审批吗？",
        session_id=session_id,
    )
    records_after_question = await submitter.list_audit_records()
    passed = (
        question.classification.intent is IntentType.UNKNOWN
        and question.submission is None
        and len(records_after_question) == 2
    )
    _print_result("question_has_no_side_effect", question, passed=passed)
    if not passed:
        failures.append("question_has_no_side_effect")

    second_session = "DAY14-SUBMISSION-ISOLATED"
    await router.route(_COMPLETE_PURCHASE, session_id=second_session)
    await router.route("确认草稿", session_id=second_session)
    isolated = await router.route("提交审批", session_id=second_session)
    passed = (
        isolated.submission is not None
        and submitted.submission is not None
        and isolated.submission.submission_result.submission_id
        != submitted.submission.submission_result.submission_id
        and _draft(isolated).draft_id != submitted_draft.draft_id
    )
    _print_result("isolated_submission", isolated, passed=passed)
    if not passed:
        failures.append("isolated_submission")

    if failures:
        raise RuntimeError("Day 14 submission verification failed: " + " | ".join(failures))


if __name__ == "__main__":
    asyncio.run(_main())
