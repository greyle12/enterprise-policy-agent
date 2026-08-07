from __future__ import annotations

from typing import Protocol, cast

from langgraph.graph import END, START, StateGraph

from app.agent.intent import IntentClassification, IntentType
from app.agent.workflow_models import (
    AgentResponseStatus,
    AgentRouteResult,
    AgentWorkflowNode,
    AgentWorkflowState,
    AgentWorkflowStep,
    AgentWorkflowTrace,
)
from app.rag.policy_answer_service import PolicyAnswer
from app.tools.approval_models import ApprovalCheckAnswer
from app.tools.draft_models import DraftGenerationAnswer
from app.tools.material_models import MaterialCheckAnswer

_WORKFLOW_NAME = "enterprise_policy_workflow"
_WORKFLOW_VERSION = "1.0"

_UNKNOWN_REPLY = (
    "我还不能确定你希望查询制度、检查材料、判断审批流程，"
    "还是生成申请草稿。请补充具体事项和目标。"
)

_ACTION_NODE_BY_INTENT = {
    IntentType.POLICY_QUERY: AgentWorkflowNode.ANSWER_POLICY,
    IntentType.MATERIAL_CHECK: AgentWorkflowNode.CHECK_MATERIALS,
    IntentType.APPROVAL_QUERY: AgentWorkflowNode.CHECK_APPROVAL,
    IntentType.DRAFT_GENERATION: AgentWorkflowNode.GENERATE_DRAFT,
    IntentType.UNKNOWN: AgentWorkflowNode.REQUEST_CLARIFICATION,
}


class IntentDetector(Protocol):
    """工作流依赖的最小意图识别接口。"""

    async def classify(self, user_input: str) -> IntentClassification:
        """识别用户输入的主要意图。"""

        ...


class PolicyQuestionAnswerer(Protocol):
    """工作流依赖的最小制度问答接口。"""

    async def answer(self, question: str) -> PolicyAnswer:
        """根据制度证据回答问题。"""

        ...


class MaterialChecker(Protocol):
    """工作流依赖的最小材料检查接口。"""

    async def check(self, user_input: str) -> MaterialCheckAnswer:
        """查询材料要求或比对用户已经提供的材料。"""

        ...


class ApprovalChecker(Protocol):
    """工作流依赖的最小审批判断接口。"""

    async def check(self, user_input: str) -> ApprovalCheckAnswer:
        """根据确定性制度规则计算审批路线。"""

        ...


class ApplicationDraftCreator(Protocol):
    """工作流依赖的最小申请草稿生成接口。"""

    async def generate(self, user_input: str) -> DraftGenerationAnswer:
        """生成未确认、未提交的结构化申请草稿。"""

        ...


def _request_from(state: AgentWorkflowState) -> str:
    request = state.get("request")
    if not isinstance(request, str) or not request:
        raise RuntimeError("workflow state is missing request")
    return request


def _classification_from(
    state: AgentWorkflowState,
) -> IntentClassification:
    classification = state.get("classification")
    if not isinstance(classification, IntentClassification):
        raise TypeError("workflow state is missing classification")
    return classification


def _trace_step(
    state: AgentWorkflowState,
    *,
    node: AgentWorkflowNode,
    outcome: str,
) -> tuple[AgentWorkflowStep, ...]:
    return (
        AgentWorkflowStep(
            sequence=len(state.get("trace_steps", ())) + 1,
            node=node,
            outcome=outcome,
        ),
    )


class AgentWorkflow:
    """用 LangGraph 显式编排意图识别与五个业务分支。"""

    def __init__(
        self,
        *,
        intent_classifier: IntentDetector,
        policy_answer_service: PolicyQuestionAnswerer,
        material_checker: MaterialChecker,
        approval_checker: ApprovalChecker,
        draft_generator: ApplicationDraftCreator,
    ) -> None:
        self._intent_classifier = intent_classifier
        self._policy_answer_service = policy_answer_service
        self._material_checker = material_checker
        self._approval_checker = approval_checker
        self._draft_generator = draft_generator
        self._graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AgentWorkflowState)
        builder.add_node(
            AgentWorkflowNode.CLASSIFY_INTENT.value,
            self._classify_intent,
        )
        builder.add_node(
            AgentWorkflowNode.ANSWER_POLICY.value,
            self._answer_policy,
        )
        builder.add_node(
            AgentWorkflowNode.CHECK_MATERIALS.value,
            self._check_materials,
        )
        builder.add_node(
            AgentWorkflowNode.CHECK_APPROVAL.value,
            self._check_approval,
        )
        builder.add_node(
            AgentWorkflowNode.GENERATE_DRAFT.value,
            self._generate_draft,
        )
        builder.add_node(
            AgentWorkflowNode.REQUEST_CLARIFICATION.value,
            self._request_clarification,
        )

        builder.add_edge(
            START,
            AgentWorkflowNode.CLASSIFY_INTENT.value,
        )
        builder.add_conditional_edges(
            AgentWorkflowNode.CLASSIFY_INTENT.value,
            self._select_action_node,
            {
                node.value: node.value
                for node in _ACTION_NODE_BY_INTENT.values()
            },
        )

        for node in _ACTION_NODE_BY_INTENT.values():
            builder.add_edge(node.value, END)

        return builder.compile()

    async def _classify_intent(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        classification = await self._intent_classifier.classify(
            _request_from(state)
        )
        return {
            "classification": classification,
            "trace_steps": _trace_step(
                state,
                node=AgentWorkflowNode.CLASSIFY_INTENT,
                outcome=classification.intent.value,
            ),
        }

    @staticmethod
    def _select_action_node(state: AgentWorkflowState) -> str:
        classification = _classification_from(state)
        try:
            return _ACTION_NODE_BY_INTENT[
                classification.intent
            ].value
        except KeyError as exc:
            raise RuntimeError(
                "unsupported intent returned by classifier: "
                f"{classification.intent}"
            ) from exc

    async def _answer_policy(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        answer = await self._policy_answer_service.answer(
            _request_from(state)
        )
        status = AgentResponseStatus.COMPLETED
        return {
            "status": status,
            "reply": answer.answer,
            "citations": answer.citations,
            "trace_steps": _trace_step(
                state,
                node=AgentWorkflowNode.ANSWER_POLICY,
                outcome=status.value,
            ),
        }

    async def _check_materials(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        answer = await self._material_checker.check(
            _request_from(state)
        )
        status = (
            AgentResponseStatus.NEEDS_CLARIFICATION
            if answer.result.clarification_question is not None
            else AgentResponseStatus.COMPLETED
        )
        return {
            "status": status,
            "reply": answer.reply,
            "citations": answer.result.citations,
            "material_check": answer.result,
            "trace_steps": _trace_step(
                state,
                node=AgentWorkflowNode.CHECK_MATERIALS,
                outcome=status.value,
            ),
        }

    async def _check_approval(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        answer = await self._approval_checker.check(
            _request_from(state)
        )
        status = (
            AgentResponseStatus.NEEDS_CLARIFICATION
            if answer.result.clarification_question is not None
            else AgentResponseStatus.COMPLETED
        )
        return {
            "status": status,
            "reply": answer.reply,
            "citations": answer.result.citations,
            "approval_check": answer.result,
            "trace_steps": _trace_step(
                state,
                node=AgentWorkflowNode.CHECK_APPROVAL,
                outcome=status.value,
            ),
        }

    async def _generate_draft(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        answer = await self._draft_generator.generate(
            _request_from(state)
        )
        status = (
            AgentResponseStatus.NEEDS_CLARIFICATION
            if answer.result.clarification_question is not None
            else AgentResponseStatus.COMPLETED
        )
        return {
            "status": status,
            "reply": answer.reply,
            "citations": answer.result.citations,
            "application_draft": answer.result,
            "trace_steps": _trace_step(
                state,
                node=AgentWorkflowNode.GENERATE_DRAFT,
                outcome=status.value,
            ),
        }

    async def _request_clarification(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        status = AgentResponseStatus.NEEDS_CLARIFICATION
        return {
            "status": status,
            "reply": _UNKNOWN_REPLY,
            "citations": (),
            "trace_steps": _trace_step(
                state,
                node=AgentWorkflowNode.REQUEST_CLARIFICATION,
                outcome=status.value,
            ),
        }

    async def run(self, user_input: str) -> AgentRouteResult:
        """执行一次无持久化状态图并返回兼容的路由结果。"""

        normalized_input = user_input.strip()
        if not normalized_input:
            raise ValueError("user_input must not be blank")

        state = cast(
            AgentWorkflowState,
            await self._graph.ainvoke(
                {"request": normalized_input}
            ),
        )
        classification = _classification_from(state)
        status = state.get("status")
        reply = state.get("reply")
        trace_steps = state.get("trace_steps", ())

        if not isinstance(status, AgentResponseStatus):
            raise TypeError("workflow state is missing response status")
        if not isinstance(reply, str) or not reply:
            raise RuntimeError("workflow state is missing reply")
        if not trace_steps:
            raise RuntimeError("workflow state is missing execution trace")

        return AgentRouteResult(
            request=normalized_input,
            classification=classification,
            status=status,
            reply=reply,
            citations=state.get("citations", ()),
            material_check=state.get("material_check"),
            approval_check=state.get("approval_check"),
            application_draft=state.get("application_draft"),
            workflow=AgentWorkflowTrace(
                name=_WORKFLOW_NAME,
                version=_WORKFLOW_VERSION,
                steps=trace_steps,
                terminal_node=trace_steps[-1].node,
            ),
        )

    def draw_mermaid(self) -> str:
        """返回编译后工作流的 Mermaid 文本，供架构检查使用。"""

        return self._graph.get_graph().draw_mermaid()
