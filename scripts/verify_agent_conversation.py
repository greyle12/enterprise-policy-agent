from __future__ import annotations

import asyncio
import json
from decimal import Decimal
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
from app.tools.draft_models import DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"

_COMPLETE_PURCHASE = (
    "采购3台27英寸办公显示器，每台2000元，"
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
        intent = IntentType.DRAFT_GENERATION if "草稿" in user_input else IntentType.UNKNOWN
        return IntentClassification(
            intent=intent,
            confidence=1.0,
            reason="Day 13 多轮会话验收分类。",
        )


class StubPolicyAnswerService:
    async def answer(self, question: str) -> PolicyAnswer:
        return PolicyAnswer(
            question=question,
            answer="本脚本不验证制度问答内容。",
            citations=(),
        )


def _build_router() -> AgentRouter:
    material_checker = RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)
    approval_checker = ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)
    return AgentRouter(
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
    )


def _draft(result):
    if result.application_draft is None or result.application_draft.draft is None:
        raise RuntimeError("verification expected a draft")
    return result.application_draft.draft


def _field_value(draft, field_name: str):
    return next(field.value for field in draft.fields if field.field_name == field_name)


def _print_result(
    name: str,
    result,
    *,
    passed: bool,
) -> None:
    draft = result.application_draft.draft if result.application_draft is not None else None
    print(
        json.dumps(
            {
                "name": name,
                "status": result.status,
                "intent": result.classification.intent,
                "session_id": (result.session.session_id if result.session is not None else None),
                "turn_number": (result.session.turn_number if result.session is not None else None),
                "phase": (result.session.phase if result.session is not None else None),
                "pending_confirmation": (
                    result.session.pending_confirmation if result.session is not None else None
                ),
                "draft_id": (draft.draft_id if draft is not None else None),
                "revision": (draft.revision if draft is not None else None),
                "user_confirmed": (draft.user_confirmed if draft is not None else None),
                "submitted": (draft.submitted if draft is not None else None),
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
    router = _build_router()
    failures: list[str] = []
    session_id = "DAY13-CONVERSATION-VERIFY"

    first = await router.route(
        "帮我生成采购申请草稿。",
        session_id=session_id,
    )
    first_draft = _draft(first)
    passed = (
        first.status is AgentResponseStatus.NEEDS_CLARIFICATION
        and first.session is not None
        and first.session.phase is AgentSessionPhase.COLLECTING_INFORMATION
        and first_draft.revision == 1
    )
    _print_result("incomplete_first_turn", first, passed=passed)
    if not passed:
        failures.append("incomplete_first_turn")

    completed = await router.route(
        "补充信息：" + _COMPLETE_PURCHASE,
        session_id=session_id,
    )
    completed_draft = _draft(completed)
    passed = (
        completed.status is AgentResponseStatus.AWAITING_CONFIRMATION
        and completed.session is not None
        and completed.session.pending_confirmation
        and completed_draft.draft_id == first_draft.draft_id
        and completed_draft.revision == 2
        and completed_draft.ready_for_confirmation
    )
    _print_result("complete_second_turn", completed, passed=passed)
    if not passed:
        failures.append("complete_second_turn")

    ambiguous = await router.route(
        "好的，我再看看",
        session_id=session_id,
    )
    passed = (
        ambiguous.status is AgentResponseStatus.NEEDS_CLARIFICATION
        and ambiguous.session is not None
        and ambiguous.session.pending_confirmation
        and ambiguous.session.turn_number == 2
    )
    _print_result("ambiguous_does_not_resume", ambiguous, passed=passed)
    if not passed:
        failures.append("ambiguous_does_not_resume")

    revised = await router.route(
        "把预计单价改为2200元",
        session_id=session_id,
    )
    revised_draft = _draft(revised)
    passed = (
        revised.status is AgentResponseStatus.AWAITING_CONFIRMATION
        and revised_draft.draft_id == first_draft.draft_id
        and revised_draft.revision == 3
        and _field_value(
            revised_draft,
            "estimated_total_amount",
        )
        == Decimal(6600)
        and revised_draft.approval_check.amount == Decimal(6600)
    )
    _print_result("revise_and_reinterrupt", revised, passed=passed)
    if not passed:
        failures.append("revise_and_reinterrupt")

    confirmed = await router.route(
        "确认草稿",
        session_id=session_id,
    )
    confirmed_draft = _draft(confirmed)
    passed = (
        confirmed.status is AgentResponseStatus.CONFIRMED
        and confirmed_draft.user_confirmed
        and not confirmed_draft.submitted
        and confirmed.session is not None
        and not confirmed.session.pending_confirmation
        and confirmed.session.phase is AgentSessionPhase.CONFIRMED
    )
    _print_result("confirm_without_submit", confirmed, passed=passed)
    if not passed:
        failures.append("confirm_without_submit")

    repeated = await router.route(
        "确认草稿",
        session_id=session_id,
    )
    repeated_draft = _draft(repeated)
    passed = (
        repeated.status is AgentResponseStatus.CONFIRMED
        and repeated_draft.confirmed_at == confirmed_draft.confirmed_at
        and repeated_draft.revision == confirmed_draft.revision
        and not repeated_draft.submitted
    )
    _print_result("idempotent_confirmation", repeated, passed=passed)
    if not passed:
        failures.append("idempotent_confirmation")

    isolated = await router.route(
        "帮我生成采购申请草稿，" + _COMPLETE_PURCHASE,
        session_id="DAY13-ISOLATED-VERIFY",
    )
    isolated_draft = _draft(isolated)
    passed = (
        isolated_draft.draft_id != first_draft.draft_id
        and not isolated_draft.user_confirmed
        and isolated.session is not None
        and isolated.session.pending_confirmation
    )
    _print_result("isolated_session", isolated, passed=passed)
    if not passed:
        failures.append("isolated_session")

    incomplete = await router.route(
        "帮我生成采购申请草稿。",
        session_id="DAY13-INCOMPLETE-VERIFY",
    )
    rejected = await router.route(
        "确认草稿",
        session_id="DAY13-INCOMPLETE-VERIFY",
    )
    rejected_draft = _draft(rejected)
    passed = (
        incomplete.status is AgentResponseStatus.NEEDS_CLARIFICATION
        and rejected.status is AgentResponseStatus.NEEDS_CLARIFICATION
        and not rejected_draft.user_confirmed
        and not rejected_draft.submitted
    )
    _print_result("reject_incomplete_confirmation", rejected, passed=passed)
    if not passed:
        failures.append("reject_incomplete_confirmation")

    if failures:
        raise RuntimeError("Day 13 conversation verification failed: " + " | ".join(failures))


if __name__ == "__main__":
    asyncio.run(_main())
