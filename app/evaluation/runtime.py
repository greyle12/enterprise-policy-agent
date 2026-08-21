from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import AgentRouter
from app.agent.workflow import IntentDetector
from app.rag.policy_answer_service import PolicyAnswer
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker

_DRAFT_CUES = (
    "生成采购申请",
    "生成差旅报销",
    "生成费用报销",
    "生成请假申请",
    "生成申请草稿",
    "填写采购申请",
    "填写报销申请",
    "填写请假申请",
    "创建申请草稿",
    "新建申请草稿",
)
_MATERIAL_CUES = (
    "哪些材料",
    "什么材料",
    "材料齐全",
    "缺什么材料",
    "还缺什么",
    "需要发票",
    "需要证明",
    "附件要求",
)
_APPROVAL_CUES = (
    "谁审批",
    "谁批准",
    "谁批",
    "走什么审批",
    "哪些审批",
    "审批流程",
    "审核流程",
    "要经过",
    "怎么审批",
)
_POLICY_DOMAIN_CUES = (
    "制度",
    "规定",
    "标准",
    "上限",
    "时限",
    "额度",
    "差旅",
    "出差",
    "采购",
    "报销",
    "请假",
    "年假",
    "病假",
    "信息安全",
    "公司数据",
    "公共大模型",
)

_EVALUATION_USER_CONTEXT = DraftUserContext(
    employee_id="EVAL-EMP-001",
    employee_name="黄金集评测用户",
    department="评测部门",
    roles=("EMPLOYEE",),
    region="中国大陆",
    identity_source="trusted_evaluation_context",
)
_EVALUATION_CLOCK = datetime(2026, 8, 8, 0, 0, tzinfo=UTC)


class OfflineIntentClassifier:
    """不调用 LLM 的关键词基线，只用于稳定回归和评测链路验收。"""

    async def classify(self, user_input: str) -> IntentClassification:
        normalized = user_input.strip()
        if not normalized:
            raise ValueError("user_input must not be blank")

        has_draft_action = "草稿" in normalized and any(
            action in normalized for action in ("生成", "填写", "创建", "新建")
        )
        if has_draft_action or any(cue in normalized for cue in _DRAFT_CUES):
            intent = IntentType.DRAFT_GENERATION
            reason = "离线基线识别到明确的申请草稿生成动作。"
        elif any(cue in normalized for cue in _MATERIAL_CUES):
            intent = IntentType.MATERIAL_CHECK
            reason = "离线基线识别到材料查询或材料完整性检查。"
        elif any(cue in normalized for cue in _APPROVAL_CUES):
            intent = IntentType.APPROVAL_QUERY
            reason = "离线基线识别到审批人或审批路径查询。"
        elif any(cue in normalized for cue in _POLICY_DOMAIN_CUES):
            intent = IntentType.POLICY_QUERY
            reason = "离线基线识别到企业制度领域的一般查询。"
        else:
            intent = IntentType.UNKNOWN
            reason = "离线基线未识别到企业制度或流程意图。"

        return IntentClassification(
            intent=intent,
            confidence=1.0,
            reason=reason,
        )


class _OfflinePolicyAnswerer:
    """路由评测占位回答器；制度引用由材料和审批用例独立评分。"""

    async def answer(self, question: str) -> PolicyAnswer:
        normalized = question.strip()
        if not normalized:
            raise ValueError("question must not be blank")
        return PolicyAnswer(
            question=normalized,
            answer=("离线评测已选择制度检索工具；本用例只评分意图和工具选择。"),
            citations=(),
        )


@dataclass(frozen=True, slots=True)
class EvaluationRuntime:
    """黄金集运行器所需的真实规则组件和 Agent Router。"""

    router: AgentRouter
    material_checker: RequiredMaterialsChecker
    approval_checker: ApprovalRuleChecker


def build_evaluation_runtime(
    *,
    policy_directory: str | Path,
    intent_classifier: IntentDetector,
) -> EvaluationRuntime:
    """构建不连接正式数据库、不会提交审批的评测运行时。"""

    material_checker = RequiredMaterialsChecker.from_policy_directory(policy_directory)
    approval_checker = ApprovalRuleChecker.from_policy_directory(policy_directory)
    draft_generator = ApplicationDraftGenerator.from_policy_directory(
        policy_directory,
        material_checker=material_checker,
        approval_checker=approval_checker,
        user_context=_EVALUATION_USER_CONTEXT,
        clock=lambda: _EVALUATION_CLOCK,
        session_id="GOLDEN-EVALUATION",
    )
    router = AgentRouter(
        intent_classifier=intent_classifier,
        policy_answer_service=_OfflinePolicyAnswerer(),
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=draft_generator,
    )
    return EvaluationRuntime(
        router=router,
        material_checker=material_checker,
        approval_checker=approval_checker,
    )
