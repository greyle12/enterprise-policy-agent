from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import AgentRouter
from app.persistence import SQLiteConversationMemoryStore
from app.rag.policy_answer_service import PolicyAnswer
from app.tools.material_check import RequiredMaterialsChecker
from app.tools.material_models import ApplicationType

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_SESSION_ID = "day19-memory-verification"


class _DeterministicIntentClassifier:
    async def classify(self, user_input: str) -> IntentClassification:
        return IntentClassification(
            intent=(
                IntentType.MATERIAL_CHECK
                if "需要哪些材料" in user_input
                else IntentType.POLICY_QUERY
            ),
            confidence=1.0,
            reason="Day 19 离线记忆验收分类。",
        )


class _OfflinePolicyAnswerService:
    async def answer(self, question: str) -> PolicyAnswer:
        return PolicyAnswer(
            question=question,
            answer="差旅住宿费应在目的地标准内凭发票报销。",
            citations=(),
        )


class _UnusedApprovalChecker:
    async def check(self, user_input: str):
        raise AssertionError(f"unexpected approval check: {user_input}")


class _UnusedDraftGenerator:
    async def generate(self, user_input: str, *, session_id=None):
        raise AssertionError(f"unexpected draft generation: {user_input}")

    async def revise(
        self,
        previous_draft,
        user_input: str,
        *,
        session_id=None,
        context_messages=(),
    ):
        raise AssertionError(f"unexpected draft revision: {user_input}")


def _build_router(database_path: Path) -> AgentRouter:
    return AgentRouter(
        intent_classifier=_DeterministicIntentClassifier(),
        policy_answer_service=_OfflinePolicyAnswerService(),
        material_checker=(RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)),
        approval_checker=_UnusedApprovalChecker(),
        draft_generator=_UnusedDraftGenerator(),
        memory_store=SQLiteConversationMemoryStore(database_path),
    )


async def run_verification(database_path: Path) -> dict[str, object]:
    """Prove contextual follow-up, restart recovery, history, and clearing."""

    await _build_router(database_path).route(
        "出差住宿费怎么报销？",
        session_id=_SESSION_ID,
    )
    restored_router = _build_router(database_path)
    follow_up = await restored_router.route(
        "那需要哪些材料？",
        session_id=_SESSION_ID,
    )
    before_clear = await restored_router.get_conversation_history(
        _SESSION_ID,
        limit=20,
    )
    await restored_router.clear_session(_SESSION_ID)
    after_clear = await restored_router.get_conversation_history(
        _SESSION_ID,
        limit=20,
    )

    application_type = (
        follow_up.material_check.application_type if follow_up.material_check is not None else None
    )
    memory = follow_up.memory
    passed = (
        memory is not None
        and memory.backend == "sqlite"
        and memory.survives_process_restart
        and memory.context_applied
        and application_type is ApplicationType.TRAVEL_REIMBURSEMENT
        and before_clear.total_message_count == 4
        and after_clear.total_message_count == 0
    )
    return {
        "passed": passed,
        "memory_backend": memory.backend if memory is not None else None,
        "survives_process_restart": (
            memory.survives_process_restart if memory is not None else False
        ),
        "follow_up_context_applied": (memory.context_applied if memory is not None else False),
        "resolved_application_type": (
            application_type.value if application_type is not None else None
        ),
        "messages_before_clear": before_clear.total_message_count,
        "messages_after_clear": after_clear.total_message_count,
    }


def main() -> None:
    with TemporaryDirectory(prefix="day19-memory-") as directory:
        report = asyncio.run(run_verification(Path(directory) / "memory.db"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
