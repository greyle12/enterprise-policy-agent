from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import AgentRouter
from app.memory import InMemoryConversationMemoryStore
from app.persistence import SQLiteConversationMemoryStore
from app.rag.policy_answer_service import PolicyAnswer
from app.tools.material_check import RequiredMaterialsChecker
from app.tools.material_models import ApplicationType

_POLICY_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "policies"


class ContextAwareIntentClassifier:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def classify(self, user_input: str) -> IntentClassification:
        self.calls.append(user_input)
        intent = (
            IntentType.MATERIAL_CHECK if "需要哪些材料" in user_input else IntentType.POLICY_QUERY
        )
        return IntentClassification(
            intent=intent,
            confidence=1.0,
            reason="Day 19 对话记忆测试分类。",
        )


class RecordingPolicyAnswerService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer(self, question: str) -> PolicyAnswer:
        self.calls.append(question)
        return PolicyAnswer(
            question=question,
            answer="差旅住宿费应在目的地标准内凭发票报销。",
            citations=(),
        )


class UnusedApprovalChecker:
    async def check(self, user_input: str):
        raise AssertionError(f"approval checker should not run: {user_input}")


class UnusedDraftGenerator:
    async def generate(self, user_input: str, *, session_id=None):
        raise AssertionError(f"draft generator should not run: {user_input}")

    async def revise(
        self,
        previous_draft,
        user_input: str,
        *,
        session_id=None,
        context_messages=(),
    ):
        raise AssertionError(f"draft generator should not run: {user_input}")


def _build_router(memory_store):
    classifier = ContextAwareIntentClassifier()
    policy_service = RecordingPolicyAnswerService()
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=policy_service,
        material_checker=(RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)),
        approval_checker=UnusedApprovalChecker(),
        draft_generator=UnusedDraftGenerator(),
        memory_store=memory_store,
    )
    return router, classifier, policy_service


@pytest.mark.asyncio
async def test_ambiguous_follow_up_uses_recent_session_context() -> None:
    router, classifier, _ = _build_router(InMemoryConversationMemoryStore())
    session_id = "agent-memory-follow-up"
    first = await router.route(
        "出差住宿费怎么报销？",
        session_id=session_id,
    )
    second = await router.route(
        "那需要哪些材料？",
        session_id=session_id,
    )

    assert first.memory is not None
    assert first.memory.context_applied is False
    assert second.request == "那需要哪些材料？"
    assert second.material_check is not None
    assert second.material_check.application_type is (ApplicationType.TRAVEL_REIMBURSEMENT)
    assert second.memory is not None
    assert second.memory.context_applied is True
    assert second.memory.context_messages_used == 2
    assert second.memory.stored_message_count == 4
    assert "出差住宿费怎么报销" in classifier.calls[1]
    assert "那需要哪些材料" in classifier.calls[1]


@pytest.mark.asyncio
async def test_memory_is_isolated_by_session_id() -> None:
    router, classifier, _ = _build_router(InMemoryConversationMemoryStore())
    await router.route(
        "出差住宿费怎么报销？",
        session_id="agent-memory-a",
    )

    result = await router.route(
        "需要哪些材料？",
        session_id="agent-memory-b",
    )

    assert classifier.calls[-1] == "需要哪些材料？"
    assert result.material_check is not None
    assert result.material_check.application_type is None
    assert result.memory is not None
    assert result.memory.context_applied is False
    first_history = await router.get_conversation_history("agent-memory-a")
    second_history = await router.get_conversation_history("agent-memory-b")
    assert first_history.total_message_count == 2
    assert second_history.total_message_count == 2


@pytest.mark.asyncio
async def test_clear_session_removes_memory_and_context() -> None:
    router, classifier, _ = _build_router(InMemoryConversationMemoryStore())
    session_id = "agent-memory-clear"
    await router.route(
        "出差住宿费怎么报销？",
        session_id=session_id,
    )

    await router.clear_session(session_id)
    result = await router.route(
        "需要哪些材料？",
        session_id=session_id,
    )

    assert classifier.calls[-1] == "需要哪些材料？"
    assert result.memory is not None
    assert result.memory.context_applied is False
    assert result.memory.stored_message_count == 2


@pytest.mark.asyncio
async def test_sqlite_context_survives_router_recreation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "agent-memory-restart.db"
    first_router, _, _ = _build_router(SQLiteConversationMemoryStore(database_path))
    session_id = "agent-memory-restart"
    await first_router.route(
        "出差住宿费怎么报销？",
        session_id=session_id,
    )

    restored_router, restored_classifier, _ = _build_router(
        SQLiteConversationMemoryStore(database_path)
    )
    result = await restored_router.route(
        "那需要哪些材料？",
        session_id=session_id,
    )

    assert "出差住宿费怎么报销" in restored_classifier.calls[0]
    assert result.material_check is not None
    assert result.material_check.application_type is (ApplicationType.TRAVEL_REIMBURSEMENT)
    assert result.memory is not None
    assert result.memory.backend == "sqlite"
    assert result.memory.survives_process_restart is True
    assert result.memory.stored_message_count == 4
