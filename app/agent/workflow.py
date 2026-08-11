from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol, cast
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import TypeAdapter

from app.agent.intent import IntentClassification, IntentType
from app.agent.workflow_models import (
    AgentResponseStatus,
    AgentRouteResult,
    AgentSessionInfo,
    AgentSessionPhase,
    AgentTurnAction,
    AgentWorkflowNode,
    AgentWorkflowState,
    AgentWorkflowStep,
    AgentWorkflowTrace,
)
from app.memory.conversation import (
    ContextualizedRequest,
    ConversationContextBuilder,
    ConversationMemoryInfo,
    ConversationMemorySnapshot,
    ConversationMemoryStore,
    InMemoryConversationMemoryStore,
)
from app.rag.policy_answer_service import PolicyAnswer
from app.tools.approval_models import ApprovalCheckAnswer
from app.tools.draft_models import (
    ApplicationDraft,
    DraftGenerationAnswer,
    DraftGenerationResult,
    DraftStatus,
    DraftUserContext,
)
from app.tools.material_models import MaterialCheckAnswer
from app.tools.mock_approval_submission import (
    SubmissionConflictError,
    SubmissionPreconditionError,
)
from app.tools.submission_models import MockApprovalSubmissionResult

_WORKFLOW_NAME = "enterprise_policy_workflow"
_WORKFLOW_VERSION = "1.4"
_SESSION_ID_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}"
)

_CHECKPOINT_ALLOWED_TYPES = (
    ("app.agent.intent", "IntentClassification"),
    ("app.agent.intent", "IntentType"),
    ("app.agent.workflow_models", "AgentResponseStatus"),
    ("app.agent.workflow_models", "AgentSessionPhase"),
    ("app.agent.workflow_models", "AgentTurnAction"),
    ("app.agent.workflow_models", "AgentWorkflowNode"),
    ("app.agent.workflow_models", "AgentWorkflowStep"),
    ("app.rag.policy_context", "PolicyCitation"),
    ("app.tools.approval_models", "ApprovalAction"),
    ("app.tools.approval_models", "ApprovalApplicationType"),
    ("app.tools.approval_models", "ApprovalCheckResult"),
    ("app.tools.approval_models", "ApprovalLevel"),
    ("app.tools.approval_models", "ApprovalStep"),
    ("app.tools.approval_models", "ApproverCode"),
    ("app.tools.draft_models", "ApplicationDraft"),
    ("app.tools.draft_models", "DraftAuditMetadata"),
    ("app.tools.draft_models", "DraftField"),
    ("app.tools.draft_models", "DraftFieldSource"),
    ("app.tools.draft_models", "DraftGenerationResult"),
    ("app.tools.draft_models", "DraftPolicySnapshot"),
    ("app.tools.draft_models", "DraftStatus"),
    ("app.tools.draft_models", "DraftUserContext"),
    ("app.tools.draft_models", "DraftValidationIssue"),
    ("app.tools.draft_models", "MissingDraftField"),
    ("app.tools.draft_models", "ValidationSeverity"),
    ("app.tools.material_models", "ApplicationType"),
    ("app.tools.material_models", "MaterialCheckMode"),
    ("app.tools.material_models", "MaterialCheckResult"),
    ("app.tools.material_models", "MaterialRequirement"),
    ("app.tools.material_models", "MissingMaterial"),
    ("app.tools.material_models", "ProvidedMaterial"),
    ("app.tools.submission_models", "ApprovalWorkflowStepStatus"),
    ("app.tools.submission_models", "MockApprovalSubmissionResult"),
    ("app.tools.submission_models", "SubmissionAuditEvent"),
    ("app.tools.submission_models", "SubmissionAuditRecord"),
    ("app.tools.submission_models", "SubmissionStatus"),
    ("app.tools.submission_models", "SubmittedApplication"),
    ("app.tools.submission_models", "SubmittedApprovalStep"),
    ("app.tools.submission_models", "SubmittedApprovalWorkflow"),
)
_DRAFT_RESULT_ADAPTER = TypeAdapter(DraftGenerationResult)

_UNKNOWN_REPLY = (
    "我还不能确定你希望查询制度、检查材料、判断审批流程，"
    "还是生成申请草稿。请补充具体事项和目标。"
)
_PENDING_CONFIRMATION_REPLY = (
    "当前草稿正在等待人工确认。请回复“确认草稿”、"
    "“取消草稿”，或明确说明要修改的字段和值。"
)

_ACTION_NODE_BY_INTENT = {
    IntentType.POLICY_QUERY: AgentWorkflowNode.ANSWER_POLICY,
    IntentType.MATERIAL_CHECK: AgentWorkflowNode.CHECK_MATERIALS,
    IntentType.APPROVAL_QUERY: AgentWorkflowNode.CHECK_APPROVAL,
    IntentType.DRAFT_GENERATION: AgentWorkflowNode.GENERATE_DRAFT,
    IntentType.UNKNOWN: AgentWorkflowNode.REQUEST_CLARIFICATION,
}

_CONFIRM_COMMANDS = {
    "确认",
    "确认草稿",
    "确认无误",
    "我确认",
    "同意",
}
_SUBMIT_COMMANDS = {
    "提交",
    "提交审批",
    "提交申请",
    "正式提交",
    "确认提交",
    "确认提交审批",
}
_CANCEL_COMMANDS = {
    "取消",
    "取消草稿",
    "放弃草稿",
    "撤销草稿",
}
_UPDATE_CUES = (
    "修改",
    "更改",
    "调整",
    "改成",
    "改为",
    "补充",
    "更新草稿",
)
_STANDALONE_REQUEST_CUES = (
    "哪些材料",
    "什么材料",
    "材料要求",
    "谁审批",
    "谁批",
    "审批流程",
    "走什么审批",
    "制度",
    "规定",
    "标准",
    "额度",
    "生成新的",
    "新建",
)


class IntentDetector(Protocol):
    """工作流依赖的最小意图识别接口。"""

    async def classify(
        self,
        user_input: str,
    ) -> IntentClassification:
        """识别用户输入的主要意图。"""

        ...


class PolicyQuestionAnswerer(Protocol):
    """工作流依赖的最小制度问答接口。"""

    async def answer(self, question: str) -> PolicyAnswer:
        """根据制度证据回答问题。"""

        ...


class MaterialChecker(Protocol):
    """工作流依赖的最小材料检查接口。"""

    async def check(
        self,
        user_input: str,
    ) -> MaterialCheckAnswer:
        """查询材料要求或比对用户已经提供的材料。"""

        ...


class ApprovalChecker(Protocol):
    """工作流依赖的最小审批判断接口。"""

    async def check(
        self,
        user_input: str,
    ) -> ApprovalCheckAnswer:
        """根据确定性制度规则计算审批路线。"""

        ...


class ApplicationDraftCreator(Protocol):
    """工作流依赖的草稿生成及修订接口。"""

    async def generate(
        self,
        user_input: str,
        *,
        session_id: str | None = None,
    ) -> DraftGenerationAnswer:
        """生成未确认、未提交的结构化申请草稿。"""

        ...

    async def revise(
        self,
        previous_draft: ApplicationDraft,
        user_input: str,
        *,
        session_id: str | None = None,
        context_messages: Sequence[str] = (),
    ) -> DraftGenerationAnswer:
        """基于当前草稿合并用户补充或修改。"""

        ...


class ApplicationSubmitter(Protocol):
    """工作流依赖的模拟审批提交接口。"""

    async def submit(
        self,
        draft: ApplicationDraft,
        *,
        confirmation_text: str,
        user_context: DraftUserContext,
        session_id: str,
        request_id: str,
        submission_idempotency_key: str,
    ) -> MockApprovalSubmissionResult:
        """验证已确认草稿并执行一次幂等的模拟提交。"""

        ...


class AgentStatePersister(Protocol):
    """Optional durable projection for session and draft query records."""

    async def save_route_state(
        self,
        session: AgentSessionInfo,
        active_draft: DraftGenerationResult | None,
    ) -> None:
        """Persist the latest session head and active draft revision."""

        ...

    async def delete_session(self, session_id: str) -> None:
        """Delete mutable session and draft projections for a reset."""

        ...


def _request_from(state: AgentWorkflowState) -> str:
    request = state.get("request")
    if not isinstance(request, str) or not request:
        raise RuntimeError("workflow state is missing request")
    return request


def _contextual_request_from(state: AgentWorkflowState) -> str:
    request = state.get("contextual_request")
    if isinstance(request, str) and request:
        return request
    return _request_from(state)


def _session_id_from(state: AgentWorkflowState) -> str:
    session_id = state.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("workflow state is missing session_id")
    return session_id


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
    steps = state.get("trace_steps", ())
    return (
        *steps,
        AgentWorkflowStep(
            sequence=len(steps) + 1,
            node=node,
            outcome=outcome,
        ),
    )


def _synthetic_classification(
    intent: IntentType,
    reason: str,
) -> IntentClassification:
    return IntentClassification(
        intent=intent,
        confidence=1.0,
        reason=reason,
    )


def _normalized_command(text: str) -> str:
    return text.strip().strip("。.!！?？ ，,")


def _explicit_draft_action(
    text: str,
) -> AgentTurnAction | None:
    command = _normalized_command(text)
    if command in _SUBMIT_COMMANDS:
        return AgentTurnAction.SUBMIT_DRAFT
    if command in _CONFIRM_COMMANDS:
        return AgentTurnAction.CONFIRM_DRAFT
    if command in _CANCEL_COMMANDS:
        return AgentTurnAction.CANCEL_DRAFT
    if any(cue in command for cue in _UPDATE_CUES):
        return AgentTurnAction.UPDATE_DRAFT
    return None


def _active_draft_from(
    state: AgentWorkflowState,
) -> DraftGenerationResult | None:
    active = state.get("active_draft")
    if not isinstance(active, DraftGenerationResult):
        return None
    return _DRAFT_RESULT_ADAPTER.validate_json(
        _DRAFT_RESULT_ADAPTER.dump_json(
            active,
            warnings=False,
        )
    )


def _infer_turn_action(
    text: str,
    active: DraftGenerationResult | None,
) -> AgentTurnAction:
    explicit = _explicit_draft_action(text)
    if explicit is AgentTurnAction.SUBMIT_DRAFT:
        return explicit

    if active is None or active.draft is None:
        return AgentTurnAction.NEW_REQUEST

    if explicit is not None:
        return explicit

    if any(cue in text for cue in _STANDALONE_REQUEST_CUES):
        return AgentTurnAction.NEW_REQUEST

    if active.draft.status in {
        DraftStatus.WAITING_FOR_INFORMATION,
        DraftStatus.WAITING_FOR_MATERIALS,
    }:
        return AgentTurnAction.UPDATE_DRAFT
    return AgentTurnAction.NEW_REQUEST


def _classification_for_action(
    action: AgentTurnAction,
) -> IntentClassification | None:
    if action is AgentTurnAction.UPDATE_DRAFT:
        return _synthetic_classification(
            IntentType.DRAFT_UPDATE,
            "当前会话存在未完成草稿，本轮用于补充或修改草稿。",
        )
    if action is AgentTurnAction.CONFIRM_DRAFT:
        return _synthetic_classification(
            IntentType.DRAFT_CONFIRMATION,
            "用户明确确认当前会话中的草稿。",
        )
    if action is AgentTurnAction.SUBMIT_DRAFT:
        return _synthetic_classification(
            IntentType.DRAFT_SUBMISSION,
            "用户明确要求提交当前会话中已经确认的草稿。",
        )
    if action is AgentTurnAction.CANCEL_DRAFT:
        return _synthetic_classification(
            IntentType.DRAFT_CANCELLATION,
            "用户明确取消当前会话中的草稿。",
        )
    return None


def _phase_for_draft(
    result: DraftGenerationResult,
) -> AgentSessionPhase:
    draft = result.draft
    if draft is None:
        return AgentSessionPhase.COLLECTING_INFORMATION
    if draft.status is DraftStatus.CONFIRMED:
        return AgentSessionPhase.CONFIRMED
    if draft.status is DraftStatus.SUBMITTED:
        return AgentSessionPhase.SUBMITTED
    if draft.status is DraftStatus.CANCELLED:
        return AgentSessionPhase.CANCELLED
    if draft.ready_for_confirmation:
        return AgentSessionPhase.AWAITING_CONFIRMATION
    return AgentSessionPhase.COLLECTING_INFORMATION


def _status_for_draft(
    result: DraftGenerationResult,
) -> AgentResponseStatus:
    if result.draft is not None and result.draft.ready_for_confirmation:
        return AgentResponseStatus.AWAITING_CONFIRMATION
    if result.clarification_question is not None:
        return AgentResponseStatus.NEEDS_CLARIFICATION
    return AgentResponseStatus.COMPLETED


def _can_await_confirmation(
    state: AgentWorkflowState,
) -> bool:
    result = state.get("application_draft")
    return (
        isinstance(result, DraftGenerationResult)
        and result.draft is not None
        and result.draft.ready_for_confirmation
        and not result.draft.user_confirmed
        and result.draft.status
        is DraftStatus.WAITING_FOR_CONFIRMATION
    )


def _submission_idempotency_key(draft: ApplicationDraft) -> str:
    digest = sha256(
        (
            f"{draft.audit_metadata.idempotency_key}\0"
            f"{draft.draft_id}\0{draft.revision}\0submit"
        ).encode()
    ).hexdigest()[:24]
    return f"submission:{draft.draft_id}:r{draft.revision}:{digest}"


def _submission_request_id(state: AgentWorkflowState) -> str:
    digest = sha256(
        (
            f"{_session_id_from(state)}\0"
            f"{state.get('turn_number', 0)}\0{_request_from(state)}"
        ).encode()
    ).hexdigest()[:16].upper()
    return f"SUBMIT-REQUEST-{digest}"


class AgentWorkflow:
    """用 checkpoint、人工中断和条件路由编排多轮办理。"""

    def __init__(
        self,
        *,
        intent_classifier: IntentDetector,
        policy_answer_service: PolicyQuestionAnswerer,
        material_checker: MaterialChecker,
        approval_checker: ApprovalChecker,
        draft_generator: ApplicationDraftCreator,
        submission_service: ApplicationSubmitter,
        checkpointer: BaseCheckpointSaver | None = None,
        state_persister: AgentStatePersister | None = None,
        memory_store: ConversationMemoryStore | None = None,
        context_builder: ConversationContextBuilder | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._intent_classifier = intent_classifier
        self._policy_answer_service = policy_answer_service
        self._material_checker = material_checker
        self._approval_checker = approval_checker
        self._draft_generator = draft_generator
        self._submission_service = submission_service
        if checkpointer is None:
            self._checkpointer = InMemorySaver(
                serde=JsonPlusSerializer(
                    allowed_msgpack_modules=(
                        _CHECKPOINT_ALLOWED_TYPES
                    )
                )
            )
        else:
            self._checkpointer = checkpointer.with_allowlist(
                _CHECKPOINT_ALLOWED_TYPES
            )
        self._state_persister = state_persister
        self._memory_store = (
            memory_store or InMemoryConversationMemoryStore()
        )
        self._context_builder = (
            context_builder or ConversationContextBuilder()
        )
        self._checkpoint_backend = str(
            getattr(
                self._checkpointer,
                "backend_name",
                "in_memory",
            )
        )
        self._survives_process_restart = bool(
            getattr(
                self._checkpointer,
                "survives_process_restart",
                False,
            )
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AgentWorkflowState)
        builder.add_node(
            AgentWorkflowNode.RESOLVE_TURN.value,
            self._resolve_turn,
        )
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
            AgentWorkflowNode.UPDATE_DRAFT.value,
            self._update_draft,
        )
        builder.add_node(
            AgentWorkflowNode.AWAIT_CONFIRMATION.value,
            self._await_confirmation,
        )
        builder.add_node(
            AgentWorkflowNode.HUMAN_CONFIRMATION_GATE.value,
            self._human_confirmation_gate,
        )
        builder.add_node(
            AgentWorkflowNode.CONFIRM_DRAFT.value,
            self._confirm_draft,
        )
        builder.add_node(
            AgentWorkflowNode.SUBMIT_APPROVAL.value,
            self._submit_approval,
        )
        builder.add_node(
            AgentWorkflowNode.CANCEL_DRAFT.value,
            self._cancel_draft,
        )
        builder.add_node(
            AgentWorkflowNode.REQUEST_CLARIFICATION.value,
            self._request_clarification,
        )

        builder.add_edge(
            START,
            AgentWorkflowNode.RESOLVE_TURN.value,
        )
        builder.add_conditional_edges(
            AgentWorkflowNode.RESOLVE_TURN.value,
            self._select_turn_node,
            {
                AgentTurnAction.NEW_REQUEST.value: (
                    AgentWorkflowNode.CLASSIFY_INTENT.value
                ),
                AgentTurnAction.UPDATE_DRAFT.value: (
                    AgentWorkflowNode.UPDATE_DRAFT.value
                ),
                AgentTurnAction.CONFIRM_DRAFT.value: (
                    AgentWorkflowNode.CONFIRM_DRAFT.value
                ),
                AgentTurnAction.SUBMIT_DRAFT.value: (
                    AgentWorkflowNode.SUBMIT_APPROVAL.value
                ),
                AgentTurnAction.CANCEL_DRAFT.value: (
                    AgentWorkflowNode.CANCEL_DRAFT.value
                ),
            },
        )
        builder.add_conditional_edges(
            AgentWorkflowNode.CLASSIFY_INTENT.value,
            self._select_action_node,
            {
                node.value: node.value
                for node in _ACTION_NODE_BY_INTENT.values()
            },
        )

        for node in (
            AgentWorkflowNode.ANSWER_POLICY,
            AgentWorkflowNode.CHECK_MATERIALS,
            AgentWorkflowNode.CHECK_APPROVAL,
            AgentWorkflowNode.REQUEST_CLARIFICATION,
            AgentWorkflowNode.CONFIRM_DRAFT,
            AgentWorkflowNode.CANCEL_DRAFT,
        ):
            builder.add_edge(node.value, END)

        for node in (
            AgentWorkflowNode.GENERATE_DRAFT,
            AgentWorkflowNode.UPDATE_DRAFT,
        ):
            builder.add_conditional_edges(
                node.value,
                self._select_after_draft,
                {
                    AgentWorkflowNode.AWAIT_CONFIRMATION.value: (
                        AgentWorkflowNode.AWAIT_CONFIRMATION.value
                    ),
                    END: END,
                },
            )

        builder.add_conditional_edges(
            AgentWorkflowNode.SUBMIT_APPROVAL.value,
            self._select_after_submission,
            {
                AgentWorkflowNode.AWAIT_CONFIRMATION.value: (
                    AgentWorkflowNode.AWAIT_CONFIRMATION.value
                ),
                END: END,
            },
        )

        builder.add_edge(
            AgentWorkflowNode.AWAIT_CONFIRMATION.value,
            AgentWorkflowNode.HUMAN_CONFIRMATION_GATE.value,
        )
        builder.add_conditional_edges(
            AgentWorkflowNode.HUMAN_CONFIRMATION_GATE.value,
            self._select_turn_node,
            {
                AgentTurnAction.UPDATE_DRAFT.value: (
                    AgentWorkflowNode.UPDATE_DRAFT.value
                ),
                AgentTurnAction.CONFIRM_DRAFT.value: (
                    AgentWorkflowNode.CONFIRM_DRAFT.value
                ),
                AgentTurnAction.SUBMIT_DRAFT.value: (
                    AgentWorkflowNode.SUBMIT_APPROVAL.value
                ),
                AgentTurnAction.CANCEL_DRAFT.value: (
                    AgentWorkflowNode.CANCEL_DRAFT.value
                ),
            },
        )

        return builder.compile(checkpointer=self._checkpointer)

    def _prepared_draft_result(
        self,
        result: DraftGenerationResult,
    ) -> DraftGenerationResult:
        """Mark drafts durable before they are written into a checkpoint."""

        draft = result.draft
        if (
            draft is None
            or not self._survives_process_restart
            or draft.audit_metadata.persisted
        ):
            return result
        return replace(
            result,
            draft=replace(
                draft,
                audit_metadata=replace(
                    draft.audit_metadata,
                    persisted=True,
                ),
            ),
        )

    async def _persist_route_state(
        self,
        state: AgentWorkflowState,
        *,
        interrupted: bool,
    ) -> AgentSessionInfo:
        session = self._session_info(
            state,
            interrupted=interrupted,
        )
        if self._state_persister is not None:
            await self._state_persister.save_route_state(
                session,
                _active_draft_from(state),
            )
        return session

    async def _resolve_turn(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        request = _request_from(state)
        action = _infer_turn_action(
            request,
            _active_draft_from(state),
        )
        phase = state.get(
            "session_phase",
            AgentSessionPhase.IDLE,
        )
        step = AgentWorkflowStep(
            sequence=1,
            node=AgentWorkflowNode.RESOLVE_TURN,
            outcome=action.value,
        )
        return {
            "turn_number": state.get("turn_number", 0) + 1,
            "session_phase": phase,
            "turn_action": action,
            "classification": _classification_for_action(action),
            "status": None,
            "reply": None,
            "citations": (),
            "material_check": None,
            "approval_check": None,
            "application_draft": None,
            "submission": None,
            "trace_steps": (step,),
        }

    @staticmethod
    def _select_turn_node(state: AgentWorkflowState) -> str:
        action = state.get("turn_action")
        if not isinstance(action, AgentTurnAction):
            raise TypeError("workflow state is missing turn action")
        return action.value

    async def _classify_intent(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        classification = await self._intent_classifier.classify(
            _contextual_request_from(state)
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

    @staticmethod
    def _select_after_draft(state: AgentWorkflowState) -> str:
        if _can_await_confirmation(state):
            return AgentWorkflowNode.AWAIT_CONFIRMATION.value
        return END

    @staticmethod
    def _select_after_submission(state: AgentWorkflowState) -> str:
        if (
            state.get("session_phase")
            is AgentSessionPhase.AWAITING_CONFIRMATION
        ):
            return AgentWorkflowNode.AWAIT_CONFIRMATION.value
        return END

    async def _answer_policy(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        answer = await self._policy_answer_service.answer(
            _contextual_request_from(state)
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
            _contextual_request_from(state)
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
            _contextual_request_from(state)
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
        request = _request_from(state)
        answer = await self._draft_generator.generate(
            _contextual_request_from(state),
            session_id=_session_id_from(state),
        )
        result = self._prepared_draft_result(answer.result)
        status = _status_for_draft(result)
        update: AgentWorkflowState = {
            "status": status,
            "reply": answer.reply,
            "citations": result.citations,
            "application_draft": result,
            "trace_steps": _trace_step(
                state,
                node=AgentWorkflowNode.GENERATE_DRAFT,
                outcome=status.value,
            ),
        }
        if result.draft is not None:
            update.update(
                {
                    "active_draft": result,
                    "draft_messages": (request,),
                    "session_phase": _phase_for_draft(
                        result
                    ),
                }
            )
        return update

    async def _update_draft(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        active = _active_draft_from(state)
        if active is None or active.draft is None:
            status = AgentResponseStatus.NEEDS_CLARIFICATION
            return {
                "classification": _synthetic_classification(
                    IntentType.DRAFT_UPDATE,
                    "用户要求修改草稿，但当前会话没有可修改草稿。",
                ),
                "status": status,
                "reply": "当前会话没有可修改的草稿，请先生成申请草稿。",
                "citations": (),
                "session_phase": AgentSessionPhase.IDLE,
                "trace_steps": _trace_step(
                    state,
                    node=AgentWorkflowNode.UPDATE_DRAFT,
                    outcome=status.value,
                ),
            }

        if active.draft.status is DraftStatus.SUBMITTED:
            status = AgentResponseStatus.NEEDS_CLARIFICATION
            return {
                "classification": _synthetic_classification(
                    IntentType.DRAFT_UPDATE,
                    "已提交草稿不可继续修改。",
                ),
                "status": status,
                "reply": (
                    "当前草稿已经提交审批，不能再修改。"
                    "如需变更，请新建一份申请草稿。"
                ),
                "citations": active.citations,
                "application_draft": active,
                "session_phase": AgentSessionPhase.SUBMITTED,
                "trace_steps": _trace_step(
                    state,
                    node=AgentWorkflowNode.UPDATE_DRAFT,
                    outcome="rejected_after_submission",
                ),
            }

        request = _request_from(state)
        messages = state.get("draft_messages", ())
        answer = await self._draft_generator.revise(
            active.draft,
            request,
            session_id=_session_id_from(state),
            context_messages=messages,
        )
        result = self._prepared_draft_result(answer.result)
        status = _status_for_draft(result)
        return {
            "classification": _synthetic_classification(
                IntentType.DRAFT_UPDATE,
                "本轮输入已合并到当前草稿并重新校验。",
            ),
            "status": status,
            "reply": answer.reply,
            "citations": result.citations,
            "application_draft": result,
            "active_draft": result,
            "draft_messages": (*messages, request),
            "session_phase": _phase_for_draft(result),
            "trace_steps": _trace_step(
                state,
                node=AgentWorkflowNode.UPDATE_DRAFT,
                outcome=status.value,
            ),
        }

    async def _await_confirmation(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        active = _active_draft_from(state)
        if active is None or active.draft is None:
            raise RuntimeError(
                "confirmation node requires an active draft"
            )
        status = AgentResponseStatus.AWAITING_CONFIRMATION
        reply = (
            f"{state.get('reply') or ''}\n"
            "请人工核对草稿；确认无误后回复“确认草稿”。"
            "你也可以说明要修改的字段，或回复“取消草稿”。"
            "确认操作不会提交审批。"
        ).strip()
        return {
            "status": status,
            "reply": reply,
            "session_phase": (
                AgentSessionPhase.AWAITING_CONFIRMATION
            ),
            "trace_steps": _trace_step(
                state,
                node=AgentWorkflowNode.AWAIT_CONFIRMATION,
                outcome=status.value,
            ),
        }

    async def _human_confirmation_gate(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        active = _active_draft_from(state)
        if active is None or active.draft is None:
            raise RuntimeError(
                "human confirmation gate requires an active draft"
            )
        decision = interrupt(
            {
                "kind": "draft_confirmation",
                "draft_id": active.draft.draft_id,
                "revision": active.draft.revision,
                "allowed_actions": [
                    AgentTurnAction.CONFIRM_DRAFT.value,
                    AgentTurnAction.SUBMIT_DRAFT.value,
                    AgentTurnAction.UPDATE_DRAFT.value,
                    AgentTurnAction.CANCEL_DRAFT.value,
                ],
            }
        )
        if not isinstance(decision, dict):
            raise TypeError("confirmation resume value must be a mapping")
        try:
            action = AgentTurnAction(decision["action"])
        except (KeyError, ValueError) as exc:
            raise ValueError(
                "unsupported confirmation resume action"
            ) from exc
        if action is AgentTurnAction.NEW_REQUEST:
            raise ValueError(
                "new_request cannot resume confirmation gate"
            )
        message = decision.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError(
                "confirmation resume message must not be blank"
            )
        return {
            "request": message.strip(),
            "turn_action": action,
            "classification": _classification_for_action(action),
            "trace_steps": _trace_step(
                state,
                node=AgentWorkflowNode.HUMAN_CONFIRMATION_GATE,
                outcome=action.value,
            ),
        }

    async def _confirm_draft(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        active = _active_draft_from(state)
        status = AgentResponseStatus.NEEDS_CLARIFICATION
        if active is None or active.draft is None:
            reply = "当前会话没有可确认的草稿，请先生成申请草稿。"
            phase = AgentSessionPhase.IDLE
            updated = None
            citations = ()
        elif active.draft.status is DraftStatus.SUBMITTED:
            status = AgentResponseStatus.SUBMITTED
            reply = (
                "当前草稿已经提交审批，无需再次确认。"
                f"模拟审批单号：{active.draft.submission_id}。"
            )
            phase = AgentSessionPhase.SUBMITTED
            updated = active
            citations = active.citations
        elif active.draft.status is DraftStatus.CANCELLED:
            reply = "当前草稿已经取消，不能再确认；请重新生成草稿。"
            phase = AgentSessionPhase.CANCELLED
            updated = active
            citations = active.citations
        elif not active.draft.ready_for_confirmation:
            reply = (
                active.clarification_question
                or "草稿信息或材料尚未齐全，暂时不能确认。"
            )
            phase = AgentSessionPhase.COLLECTING_INFORMATION
            updated = active
            citations = active.citations
        else:
            status = AgentResponseStatus.CONFIRMED
            confirmed_at = active.draft.confirmed_at
            if confirmed_at is None:
                confirmed_at = self._clock()
                if confirmed_at.tzinfo is None:
                    confirmed_at = confirmed_at.replace(tzinfo=UTC)
            confirmed_draft = replace(
                active.draft,
                status=DraftStatus.CONFIRMED,
                confirmation_required=False,
                user_confirmed=True,
                submitted=False,
                confirmed_at=confirmed_at,
                cancelled_at=None,
                submission_id=None,
                submitted_at=None,
                warnings=tuple(
                    dict.fromkeys(
                        (
                            *(
                                warning
                                for warning in active.draft.warnings
                                if warning
                                != "草稿尚未确认，也没有提交审批。"
                            ),
                            "草稿已由用户确认，但尚未提交审批。",
                        )
                    )
                ),
            )
            updated = replace(
                active,
                draft=confirmed_draft,
                clarification_question=None,
            )
            reply = (
                f"已确认{confirmed_draft.title}（第"
                f"{confirmed_draft.revision}版）。"
                "当前仍未提交审批；后续提交必须使用单独的提交动作。"
            )
            phase = AgentSessionPhase.CONFIRMED
            citations = updated.citations

        update: AgentWorkflowState = {
            "classification": _synthetic_classification(
                IntentType.DRAFT_CONFIRMATION,
                "用户明确确认当前会话中的草稿。",
            ),
            "status": status,
            "reply": reply,
            "citations": citations,
            "session_phase": phase,
            "trace_steps": _trace_step(
                state,
                node=AgentWorkflowNode.CONFIRM_DRAFT,
                outcome=status.value,
            ),
        }
        if updated is not None:
            updated = self._prepared_draft_result(updated)
            update.update(
                {
                    "application_draft": updated,
                    "active_draft": updated,
                }
            )
        return update

    async def _submit_approval(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        active = _active_draft_from(state)
        classification = _synthetic_classification(
            IntentType.DRAFT_SUBMISSION,
            "用户明确要求提交当前会话中已经确认的草稿。",
        )
        if active is None or active.draft is None:
            status = AgentResponseStatus.NEEDS_CLARIFICATION
            return {
                "classification": classification,
                "status": status,
                "reply": "当前会话没有可提交的草稿，请先生成并确认草稿。",
                "citations": (),
                "session_phase": AgentSessionPhase.IDLE,
                "trace_steps": _trace_step(
                    state,
                    node=AgentWorkflowNode.SUBMIT_APPROVAL,
                    outcome="draft_missing",
                ),
            }

        draft = active.draft
        if draft.status is DraftStatus.CANCELLED:
            status = AgentResponseStatus.NEEDS_CLARIFICATION
            return {
                "classification": classification,
                "status": status,
                "reply": "当前草稿已经取消，不能提交；请重新生成草稿。",
                "citations": active.citations,
                "application_draft": active,
                "session_phase": AgentSessionPhase.CANCELLED,
                "trace_steps": _trace_step(
                    state,
                    node=AgentWorkflowNode.SUBMIT_APPROVAL,
                    outcome="draft_cancelled",
                ),
            }

        if draft.status not in {
            DraftStatus.CONFIRMED,
            DraftStatus.SUBMITTED,
        }:
            status = AgentResponseStatus.NEEDS_CLARIFICATION
            phase = _phase_for_draft(active)
            reply = (
                "草稿尚未经过明确确认，请先回复“确认草稿”，"
                "确认成功后再单独回复“提交审批”。"
                if draft.ready_for_confirmation
                else (
                    active.clarification_question
                    or "草稿信息或材料尚未齐全，暂时不能提交审批。"
                )
            )
            return {
                "classification": classification,
                "status": status,
                "reply": reply,
                "citations": active.citations,
                "application_draft": active,
                "session_phase": phase,
                "trace_steps": _trace_step(
                    state,
                    node=AgentWorkflowNode.SUBMIT_APPROVAL,
                    outcome="draft_confirmation_required",
                ),
            }

        try:
            submission = await self._submission_service.submit(
                draft,
                confirmation_text=_request_from(state),
                user_context=draft.applicant,
                session_id=_session_id_from(state),
                request_id=_submission_request_id(state),
                submission_idempotency_key=(
                    _submission_idempotency_key(draft)
                ),
            )
        except SubmissionPreconditionError as exc:
            status = AgentResponseStatus.NEEDS_CLARIFICATION
            return {
                "classification": classification,
                "status": status,
                "reply": exc.user_message,
                "citations": active.citations,
                "application_draft": active,
                "session_phase": _phase_for_draft(active),
                "trace_steps": _trace_step(
                    state,
                    node=AgentWorkflowNode.SUBMIT_APPROVAL,
                    outcome=exc.code,
                ),
            }
        except SubmissionConflictError:
            status = AgentResponseStatus.UNAVAILABLE
            return {
                "classification": classification,
                "status": status,
                "reply": (
                    "检测到提交幂等状态冲突，未创建新的审批申请。"
                    "请保留当前会话并联系系统管理员检查。"
                ),
                "citations": active.citations,
                "application_draft": active,
                "session_phase": _phase_for_draft(active),
                "trace_steps": _trace_step(
                    state,
                    node=AgentWorkflowNode.SUBMIT_APPROVAL,
                    outcome="idempotency_conflict",
                ),
            }

        submitted = submission.submission_result
        submitted_draft = replace(
            draft,
            status=DraftStatus.SUBMITTED,
            ready_for_confirmation=False,
            confirmation_required=False,
            user_confirmed=True,
            submitted=True,
            submission_id=submitted.submission_id,
            submitted_at=submitted.submitted_at,
            cancelled_at=None,
            warnings=tuple(
                dict.fromkeys(
                    (
                        *(
                            warning
                            for warning in draft.warnings
                            if warning
                            not in {
                                "草稿尚未确认，也没有提交审批。",
                                "草稿已由用户确认，但尚未提交审批。",
                            }
                        ),
                        (
                            "草稿已模拟提交审批；提交和审计记录已写入SQLite。"
                            if self._survives_process_restart
                            else (
                                "草稿已模拟提交审批；提交和审计记录"
                                "仅保存在当前进程内。"
                            )
                        ),
                    )
                )
            ),
        )
        updated = replace(
            active,
            draft=submitted_draft,
            clarification_question=None,
        )
        updated = self._prepared_draft_result(updated)
        status = AgentResponseStatus.SUBMITTED
        reply = (
            (
                "该草稿此前已经提交，本次请求按幂等规则返回首次提交结果，"
                "没有创建新的审批申请。"
            )
            if submission.duplicate_submission
            else "草稿已成功模拟提交审批。"
        )
        reply += (
            f"模拟审批单号：{submitted.submission_id}；"
            f"当前状态：{submitted.status.value}。"
        )
        return {
            "classification": classification,
            "status": status,
            "reply": reply,
            "citations": updated.citations,
            "application_draft": updated,
            "active_draft": updated,
            "submission": submission,
            "session_phase": AgentSessionPhase.SUBMITTED,
            "trace_steps": _trace_step(
                state,
                node=AgentWorkflowNode.SUBMIT_APPROVAL,
                outcome=(
                    "idempotent_replay"
                    if submission.duplicate_submission
                    else status.value
                ),
            ),
        }

    async def _cancel_draft(
        self,
        state: AgentWorkflowState,
    ) -> AgentWorkflowState:
        active = _active_draft_from(state)
        if active is None or active.draft is None:
            status = AgentResponseStatus.NEEDS_CLARIFICATION
            reply = "当前会话没有可取消的草稿。"
            updated = None
            citations = ()
            phase = AgentSessionPhase.IDLE
        elif active.draft.status is DraftStatus.SUBMITTED:
            status = AgentResponseStatus.NEEDS_CLARIFICATION
            reply = (
                "当前草稿已经提交审批，不能再按草稿取消。"
                "本阶段尚未实现审批撤回。"
            )
            updated = active
            citations = active.citations
            phase = AgentSessionPhase.SUBMITTED
        else:
            cancelled_at = self._clock()
            if cancelled_at.tzinfo is None:
                cancelled_at = cancelled_at.replace(tzinfo=UTC)
            cancelled_draft = replace(
                active.draft,
                status=DraftStatus.CANCELLED,
                ready_for_confirmation=False,
                confirmation_required=False,
                user_confirmed=False,
                submitted=False,
                confirmed_at=None,
                cancelled_at=cancelled_at,
                submission_id=None,
                submitted_at=None,
            )
            updated = replace(
                active,
                draft=cancelled_draft,
                clarification_question=None,
            )
            status = AgentResponseStatus.CANCELLED
            reply = (
                f"已取消{cancelled_draft.title}。"
                "该草稿没有提交审批。"
            )
            citations = updated.citations
            phase = AgentSessionPhase.CANCELLED

        update: AgentWorkflowState = {
            "classification": _synthetic_classification(
                IntentType.DRAFT_CANCELLATION,
                "用户明确取消当前会话中的草稿。",
            ),
            "status": status,
            "reply": reply,
            "citations": citations,
            "session_phase": phase,
            "trace_steps": _trace_step(
                state,
                node=AgentWorkflowNode.CANCEL_DRAFT,
                outcome=status.value,
            ),
        }
        if updated is not None:
            updated = self._prepared_draft_result(updated)
            update.update(
                {
                    "application_draft": updated,
                    "active_draft": updated,
                }
            )
        return update

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

    @staticmethod
    def _config(session_id: str) -> dict[str, object]:
        return {
            "configurable": {
                "thread_id": session_id,
            }
        }

    @staticmethod
    def _validate_or_create_session_id(
        session_id: str | None,
    ) -> str:
        if session_id is None:
            return f"session-{uuid4().hex}"
        normalized = session_id.strip()
        if not _SESSION_ID_PATTERN.fullmatch(normalized):
            raise ValueError(
                "session_id must contain 1-64 letters, numbers, "
                "dots, underscores, colons, or hyphens"
            )
        return normalized

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    def _session_info(
        self,
        state: AgentWorkflowState,
        *,
        interrupted: bool,
    ) -> AgentSessionInfo:
        active = _active_draft_from(state)
        draft = active.draft if active is not None else None
        phase = state.get(
            "session_phase",
            AgentSessionPhase.IDLE,
        )
        return AgentSessionInfo(
            session_id=_session_id_from(state),
            turn_number=state.get("turn_number", 0),
            phase=phase,
            active_draft_id=(
                draft.draft_id if draft is not None else None
            ),
            draft_revision=(
                draft.revision if draft is not None else None
            ),
            pending_confirmation=interrupted,
            checkpoint_backend=self._checkpoint_backend,
            survives_process_restart=(
                self._survives_process_restart
            ),
        )

    def _result_from_state(
        self,
        state: AgentWorkflowState,
        *,
        interrupted: bool,
        session: AgentSessionInfo | None = None,
    ) -> AgentRouteResult:
        classification = _classification_from(state)
        status = state.get("status")
        reply = state.get("reply")
        trace_steps = state.get("trace_steps", ())
        if not isinstance(status, AgentResponseStatus):
            raise TypeError("workflow state is missing response status")
        if not isinstance(reply, str) or not reply:
            raise RuntimeError("workflow state is missing reply")
        if not trace_steps:
            raise RuntimeError(
                "workflow state is missing execution trace"
            )

        return AgentRouteResult(
            request=_request_from(state),
            classification=classification,
            status=status,
            reply=reply,
            citations=state.get("citations", ()),
            material_check=state.get("material_check"),
            approval_check=state.get("approval_check"),
            application_draft=state.get("application_draft"),
            submission=state.get("submission"),
            workflow=AgentWorkflowTrace(
                name=_WORKFLOW_NAME,
                version=_WORKFLOW_VERSION,
                steps=trace_steps,
                terminal_node=trace_steps[-1].node,
                interrupted=interrupted,
            ),
            session=(
                session
                if session is not None
                else self._session_info(
                    state,
                    interrupted=interrupted,
                )
            ),
        )

    def _pending_input_result(
        self,
        state: AgentWorkflowState,
        request: str,
    ) -> AgentRouteResult:
        active = _active_draft_from(state)
        step = AgentWorkflowStep(
            sequence=1,
            node=AgentWorkflowNode.RESOLVE_TURN,
            outcome="confirmation_input_required",
        )
        return AgentRouteResult(
            request=request,
            classification=_synthetic_classification(
                IntentType.UNKNOWN,
                "当前草稿正在等待明确的确认、修改或取消指令。",
            ),
            status=AgentResponseStatus.NEEDS_CLARIFICATION,
            reply=_PENDING_CONFIRMATION_REPLY,
            citations=(
                active.citations if active is not None else ()
            ),
            application_draft=active,
            workflow=AgentWorkflowTrace(
                name=_WORKFLOW_NAME,
                version=_WORKFLOW_VERSION,
                steps=(step,),
                terminal_node=AgentWorkflowNode.RESOLVE_TURN,
                interrupted=True,
            ),
            session=self._session_info(
                state,
                interrupted=True,
            ),
        )

    async def _record_memory(
        self,
        result: AgentRouteResult,
        contextualized: ContextualizedRequest,
    ) -> AgentRouteResult:
        session = result.session
        if session is None:
            raise RuntimeError("Agent result is missing session metadata")
        await self._memory_store.append_turn(
            session.session_id,
            user_message=result.request,
            assistant_message=result.reply,
        )
        snapshot = await self._memory_store.get_snapshot(
            session.session_id,
            limit=self._context_builder.message_limit,
        )
        return replace(
            result,
            memory=ConversationMemoryInfo(
                backend=snapshot.backend,
                stored_message_count=snapshot.total_message_count,
                context_applied=contextualized.context_applied,
                context_messages_used=(
                    contextualized.context_messages_used
                ),
                context_window_limit=(
                    self._context_builder.message_limit
                ),
                survives_process_restart=(
                    snapshot.survives_process_restart
                ),
            ),
        )

    async def run(
        self,
        user_input: str,
        *,
        session_id: str | None = None,
    ) -> AgentRouteResult:
        """执行或恢复一个由 session_id 隔离的 LangGraph 会话。"""

        normalized_input = user_input.strip()
        if not normalized_input:
            raise ValueError("user_input must not be blank")
        resolved_session_id = self._validate_or_create_session_id(
            session_id
        )
        config = self._config(resolved_session_id)

        async with self._lock_for(resolved_session_id):
            snapshot = await self._graph.aget_state(config)
            snapshot_state = cast(
                AgentWorkflowState,
                dict(snapshot.values),
            )
            pending_confirmation = (
                AgentWorkflowNode.HUMAN_CONFIRMATION_GATE.value
                in snapshot.next
            )
            memory_snapshot = await self._memory_store.get_snapshot(
                resolved_session_id,
                limit=self._context_builder.message_limit,
            )
            if pending_confirmation:
                contextualized = ContextualizedRequest(
                    original=normalized_input,
                    resolved=normalized_input,
                    context_applied=False,
                    context_messages_used=0,
                    reason="confirmation_gate",
                )
                action = _explicit_draft_action(normalized_input)
                if action is None:
                    result = self._pending_input_result(
                        snapshot_state,
                        normalized_input,
                    )
                    return await self._record_memory(
                        result,
                        contextualized,
                    )
                turn_number = snapshot_state.get(
                    "turn_number",
                    0,
                ) + 1
                resume_state: AgentWorkflowState = {
                    "request": normalized_input,
                    "contextual_request": normalized_input,
                    "turn_number": turn_number,
                    "turn_action": action,
                    "classification": (
                        _classification_for_action(action)
                    ),
                    "status": None,
                    "reply": None,
                    "citations": (),
                    "material_check": None,
                    "approval_check": None,
                    "application_draft": None,
                    "submission": None,
                    "trace_steps": (
                        AgentWorkflowStep(
                            sequence=1,
                            node=AgentWorkflowNode.RESOLVE_TURN,
                            outcome=action.value,
                        ),
                    ),
                }
                raw_state = await self._graph.ainvoke(
                    Command(
                        update=resume_state,
                        resume={
                            "action": action.value,
                            "message": normalized_input,
                        },
                    ),
                    config,
                )
            else:
                contextualized = self._context_builder.build(
                    normalized_input,
                    memory_snapshot.messages,
                )
                raw_state = await self._graph.ainvoke(
                    {
                        "request": normalized_input,
                        "contextual_request": contextualized.resolved,
                        "session_id": resolved_session_id,
                    },
                    config,
                )

            if not isinstance(raw_state, dict):
                raise TypeError(
                    "workflow invocation did not return state mapping"
                )
            state = cast(AgentWorkflowState, raw_state)
            interrupted = "__interrupt__" in raw_state
            session = await self._persist_route_state(
                state,
                interrupted=interrupted,
            )
            result = self._result_from_state(
                state,
                interrupted=interrupted,
                session=session,
            )
            return await self._record_memory(result, contextualized)

    async def get_memory_snapshot(
        self,
        session_id: str,
        *,
        limit: int = 20,
    ) -> ConversationMemorySnapshot:
        """Return recent sanitized messages for one validated session."""

        resolved = self._validate_or_create_session_id(session_id)
        return await self._memory_store.get_snapshot(
            resolved,
            limit=limit,
        )

    async def clear_session(self, session_id: str) -> None:
        """删除一个内存 checkpoint；主要供测试和演示重置使用。"""

        resolved = self._validate_or_create_session_id(session_id)
        async with self._lock_for(resolved):
            await self._checkpointer.adelete_thread(resolved)
            if self._state_persister is not None:
                await self._state_persister.delete_session(resolved)
            await self._memory_store.clear_session(resolved)
        self._session_locks.pop(resolved, None)

    def draw_mermaid(self) -> str:
        """返回编译后工作流的 Mermaid 文本，供架构检查使用。"""

        return self._graph.get_graph().draw_mermaid()
