from __future__ import annotations

import asyncio
import json
from typing import Any

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import AgentResponseStatus, AgentRouter
from app.rag.policy_answer_service import PolicyAnswer
from app.resilience import (
    ResilientToolExecutor,
    ToolCallOutcome,
    ToolExecutionError,
    ToolName,
    ToolOperationKind,
)

_SENSITIVE_ERROR_TEXT = "api_key=day20-secret-must-not-leak"


class _StaticPolicyClassifier:
    async def classify(self, user_input: str) -> IntentClassification:
        return IntentClassification(
            intent=IntentType.POLICY_QUERY,
            confidence=1.0,
            reason="Day 20 离线验收分类。",
        )


class _FlakyPolicyService:
    def __init__(self, *, fail_attempts: int) -> None:
        self.fail_attempts = fail_attempts
        self.calls = 0

    async def answer(self, question: str) -> PolicyAnswer:
        self.calls += 1
        if self.calls <= self.fail_attempts:
            raise ConnectionError(_SENSITIVE_ERROR_TEXT)
        return PolicyAnswer(
            question=question,
            answer="只读制度工具在瞬时故障后恢复。",
            citations=(),
        )


class _UnusedTool:
    async def check(self, user_input: str):
        raise AssertionError(f"unused check tool called: {user_input}")

    async def generate(self, user_input: str, *, session_id=None):
        raise AssertionError(f"unused draft tool called: {user_input}")

    async def revise(
        self,
        previous_draft,
        user_input: str,
        *,
        session_id=None,
        context_messages=(),
    ):
        raise AssertionError(f"unused draft revision called: {user_input}")


def _executor() -> ResilientToolExecutor:
    return ResilientToolExecutor(
        safe_tool_timeout_seconds=0.2,
        mutation_tool_timeout_seconds=0.2,
        max_attempts=3,
        retry_min_wait_seconds=0,
        retry_max_wait_seconds=0,
        error_id_factory=lambda: "ERR-DAY20VERIFY",
    )


def _router(policy_service: _FlakyPolicyService) -> AgentRouter:
    unused = _UnusedTool()
    return AgentRouter(
        intent_classifier=_StaticPolicyClassifier(),
        policy_answer_service=policy_service,
        material_checker=unused,
        approval_checker=unused,
        draft_generator=unused,
        tool_executor=_executor(),
    )


async def _run_verification() -> dict[str, Any]:
    recovered_service = _FlakyPolicyService(fail_attempts=2)
    recovered = await _router(recovered_service).route(
        "差旅住宿标准是多少？",
        session_id="day20-verify-recovered",
    )
    if recovered.resilience is None:
        raise RuntimeError("recovered result is missing resilience metadata")
    recovered_record = recovered.resilience.tool_calls[-1]

    unavailable_service = _FlakyPolicyService(fail_attempts=99)
    unavailable = await _router(unavailable_service).route(
        "差旅住宿标准是多少？",
        session_id="day20-verify-unavailable",
    )
    if unavailable.resilience is None:
        raise RuntimeError("unavailable result is missing resilience metadata")
    unavailable_record = unavailable.resilience.tool_calls[-1]
    if unavailable_record.error is None:
        raise RuntimeError("failed tool record is missing safe error")

    mutation_calls = 0

    async def unavailable_mutation() -> str:
        nonlocal mutation_calls
        mutation_calls += 1
        raise ConnectionError(_SENSITIVE_ERROR_TEXT)

    try:
        await _executor().execute(
            tool=ToolName.APPROVAL_SUBMISSION,
            operation=ToolOperationKind.MUTATION,
            call=unavailable_mutation,
        )
    except ToolExecutionError as exc:
        mutation_record = exc.record
    else:
        raise RuntimeError("unavailable mutation unexpectedly succeeded")

    sensitive_error_exposed = _SENSITIVE_ERROR_TEXT in "\n".join(
        (
            recovered.reply,
            unavailable.reply,
            unavailable_record.error.user_message,
            str(mutation_record.error),
        )
    )
    passed = all(
        (
            recovered.status is AgentResponseStatus.COMPLETED,
            recovered_record.outcome is ToolCallOutcome.RECOVERED,
            recovered_record.attempts == 3,
            unavailable.status is AgentResponseStatus.UNAVAILABLE,
            unavailable_record.outcome is ToolCallOutcome.FAILED,
            unavailable_record.attempts == 3,
            mutation_calls == 1,
            mutation_record.max_attempts == 1,
            mutation_record.retry_safe is False,
            not sensitive_error_exposed,
        )
    )
    return {
        "passed": passed,
        "workflow_version": (
            recovered.workflow.version if recovered.workflow is not None else None
        ),
        "read_only_recovery": {
            "tool": recovered_record.tool.value,
            "attempts": recovered_record.attempts,
            "outcome": recovered_record.outcome.value,
        },
        "safe_degradation": {
            "status": unavailable.status.value,
            "attempts": unavailable_record.attempts,
            "error_code": unavailable_record.error.code,
            "sensitive_error_exposed": sensitive_error_exposed,
        },
        "mutation_guard": {
            "tool": mutation_record.tool.value,
            "attempts": mutation_record.attempts,
            "max_attempts": mutation_record.max_attempts,
            "retry_safe": mutation_record.retry_safe,
            "recovery_action": (
                mutation_record.error.recovery_action.value
                if mutation_record.error is not None
                else None
            ),
        },
    }


def run_verification() -> dict[str, Any]:
    """运行完全离线的 Day 20 重试、降级和提交保护验收。"""

    return asyncio.run(_run_verification())


def main() -> int:
    result = run_verification()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
