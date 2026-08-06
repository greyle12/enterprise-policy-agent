from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.agent.intent import IntentClassification, IntentType
from app.rag.policy_answer_service import PolicyAnswer
from app.rag.policy_context import PolicyCitation


class AgentResponseStatus(StrEnum):
    """统一 Agent 入口可能返回的处理状态。"""

    COMPLETED = "completed"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNAVAILABLE = "unavailable"


class IntentDetector(Protocol):
    """AgentRouter 依赖的最小意图识别接口。"""

    async def classify(
        self,
        user_input: str,
    ) -> IntentClassification:
        """识别用户输入的主要意图。"""

        ...


class PolicyQuestionAnswerer(Protocol):
    """AgentRouter 依赖的最小制度问答接口。"""

    async def answer(
        self,
        question: str,
    ) -> PolicyAnswer:
        """根据制度证据回答问题。"""

        ...


@dataclass(frozen=True, slots=True)
class AgentRouteResult:
    """统一 Agent 路由的一次结构化结果。"""

    request: str
    classification: IntentClassification
    status: AgentResponseStatus
    reply: str
    citations: tuple[PolicyCitation, ...] = ()


_UNKNOWN_REPLY = (
    "我还不能确定你希望查询制度、检查材料、判断审批流程，"
    "还是生成申请草稿。请补充具体事项和目标。"
)

_UNAVAILABLE_REPLIES = {
    IntentType.MATERIAL_CHECK: (
        "已识别为材料检查请求，但材料检查能力暂不可用。"
    ),
    IntentType.APPROVAL_QUERY: (
        "已识别为审批流程查询，但审批判断能力暂不可用。"
    ),
    IntentType.DRAFT_GENERATION: (
        "已识别为申请草稿生成请求，但草稿生成能力暂不可用。"
    ),
}


class AgentRouter:
    """先识别意图，再把请求交给对应业务能力。"""

    def __init__(
        self,
        *,
        intent_classifier: IntentDetector,
        policy_answer_service: PolicyQuestionAnswerer,
    ) -> None:
        self._intent_classifier = intent_classifier
        self._policy_answer_service = policy_answer_service

    async def route(
        self,
        user_input: str,
    ) -> AgentRouteResult:
        """路由一次用户请求，未接入的能力不会误执行。"""

        normalized_input = user_input.strip()

        if not normalized_input:
            raise ValueError("user_input must not be blank")

        classification = await self._intent_classifier.classify(
            normalized_input
        )

        if classification.intent is IntentType.POLICY_QUERY:
            answer = await self._policy_answer_service.answer(
                normalized_input
            )

            return AgentRouteResult(
                request=normalized_input,
                classification=classification,
                status=AgentResponseStatus.COMPLETED,
                reply=answer.answer,
                citations=answer.citations,
            )

        if classification.intent is IntentType.UNKNOWN:
            return AgentRouteResult(
                request=normalized_input,
                classification=classification,
                status=(
                    AgentResponseStatus.NEEDS_CLARIFICATION
                ),
                reply=_UNKNOWN_REPLY,
            )

        return AgentRouteResult(
            request=normalized_input,
            classification=classification,
            status=AgentResponseStatus.UNAVAILABLE,
            reply=_UNAVAILABLE_REPLIES[
                classification.intent
            ],
        )
