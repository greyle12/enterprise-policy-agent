"""Capture actual Router execution with an injected, budgeted evaluation provider."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from pydantic import Field

from app.agent.intent_classifier import IntentClassifier
from app.agent.router import AgentRouter
from app.evaluation.judge import JudgeBudget
from app.evaluation.models import _StrictModel
from app.evaluation.workflow_fixtures import WorkflowFixture
from app.evaluation.workflow_evaluation import evaluate_workflow_case
from app.persistence import (
    SQLiteAgentStateStore,
    SQLiteCheckpointSaver,
    SQLiteMockApprovalSubmitter,
)
from app.rag.policy_answer_service import PolicyAnswerService, _SYSTEM_PROMPT
from app.resilience import ResilientToolExecutor
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker


class CaptureCase(_StrictModel):
    case_id: str = Field(pattern=r"^CAP-[0-9]{3}$")
    access: Literal["allowed", "denied"] = "allowed"
    queries: tuple[str, ...] = Field(min_length=1)


def load_capture_cases(path):
    cases = [
        CaptureCase.model_validate_json(s)
        for s in Path(path).read_text("utf-8").splitlines()
        if s.strip()
    ]
    if (
        not cases
        or len({c.case_id for c in cases}) != len(cases)
        or any(not q.strip() for c in cases for q in c.queries)
    ):
        raise ValueError("empty or duplicate capture cases/queries")
    return cases


class CaptureBudgetExceeded(RuntimeError):
    pass


AnswerPromptVariant = Literal["baseline", "completeness-v1"]
ANSWER_PROMPT_SUFFIX = """

作答前逐项核对证据中的适用对象、前提、例外、必需材料和审批条件，只保留影响当前问题结论的项目。
不得遗漏会改变结论的条件，也不得把不同条款拼接成证据未规定的新要求。
证据没有覆盖的部分明确说明无法确定；不要把“未提及”表述为“允许”或“不需要”。
答案先给结论，再列必要条件；每项事实紧随支持它的来源。只输出答案，不输出内部核对过程。
""".rstrip()


def capture_answer_prompt(variant: AnswerPromptVariant = "baseline"):
    if variant not in {"baseline", "completeness-v1"}:
        raise ValueError("unknown capture answer prompt variant")
    return _SYSTEM_PROMPT + (ANSWER_PROMPT_SUFFIX if variant == "completeness-v1" else "")


class BudgetedCaptureClient:
    """One shared budget across cases, turns and classifier/answer calls; no retries."""

    def __init__(self, provider, budget: JudgeBudget, *, answer_prompt_variant="baseline"):
        self.answer_prompt = capture_answer_prompt(answer_prompt_variant)
        self.provider, self.budget = provider, budget
        self.calls = self.reserved_tokens = 0
        self.records = []
        self._lock = asyncio.Lock()

    async def chat(self, messages):
        request = [dict(m) for m in messages]
        if request and request[0] == {"role": "system", "content": _SYSTEM_PROMPT}:
            request[0]["content"] = self.answer_prompt
        reserve = (
            len(json.dumps(request, ensure_ascii=False).encode())
            + 1024
            + self.budget.max_output_tokens
        )
        record = {"messages": request, "reserved_tokens": reserve, "provider_called": False}
        self.records.append(record)
        async with self._lock:
            exhausted = (
                self.calls >= self.budget.max_calls
                or self.reserved_tokens + reserve > self.budget.token_limit
                or (self.reserved_tokens + reserve) * self.budget.max_usd_per_million / 1e6
                > self.budget.cost_limit
            )
            if exhausted:
                record["status"] = "budget_exhausted"
                raise CaptureBudgetExceeded("evaluation budget exhausted")
            self.calls += 1
            self.reserved_tokens += reserve
            record["provider_called"] = True
        started = perf_counter()
        try:
            text, tokens = await asyncio.wait_for(
                self.provider.complete(request, self.budget.max_output_tokens),
                self.budget.timeout_seconds,
            )
            if tokens is not None and (type(tokens) is not int or tokens < 0 or tokens > reserve):
                async with self._lock:
                    self.reserved_tokens = self.budget.token_limit
                raise ValueError("invalid provider usage")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty provider output")
            record.update(status="ok", response=text, tokens=tokens)
            return text
        except BaseException as exc:
            record.update(
                status="cancelled" if isinstance(exc, asyncio.CancelledError) else "provider_error",
                error_type=type(exc).__name__,
            )
            raise
        finally:
            record["latency_ms"] = (perf_counter() - started) * 1000


class CaptureWorkflowFixture(WorkflowFixture):
    """Keep existing fault/ACL semantics while replacing classifier and answer model calls."""

    def __init__(self, name, policies, client):
        super().__init__(name, policies)
        self.client = client
        self.classifier = IntentClassifier(llm_client=client)
        self.executor = ResilientToolExecutor(
            safe_tool_timeout_seconds=client.budget.timeout_seconds + 1,
            max_attempts=3,
            retry_min_wait_seconds=0,
            retry_max_wait_seconds=0,
        )

    async def chat(self, messages):
        self.messages.append(list(messages))
        if self.name == "timeout_policy" and len(self.messages) == 1:
            raise TimeoutError("synthetic provider timeout")
        return await self.client.chat(messages)


async def turn_snapshot(
    index, query, result, error, session_id, store, submitter, records, retrieved_ids
):
    session = await store.get_session(session_id)
    draft = (
        await store.get_draft(session.active_draft_id)
        if session and session.active_draft_id
        else None
    )
    submission = (
        await submitter.get_submission(draft_id=session.active_draft_id)
        if session and session.active_draft_id
        else None
    )
    audit = [r for r in await submitter.list_audit_records() if r.session_id == session_id]
    return jsonable_encoder(
        {
            "turn": index,
            "query": query,
            "result": result,
            "error_type": error,
            "persisted_session": session,
            "persisted_draft": draft,
            "persisted_submission": submission,
            "audit_records": audit,
            "provider_records": records,
            "retrieved_chunk_ids": retrieved_ids,
        }
    )


async def capture_workflow_cases(cases, client, directory, policy_directory, on_case=None):
    captures, evaluations = [], []
    for case in cases:
        database = Path(directory) / (uuid4().hex + ".db")
        store, submitter = SQLiteAgentStateStore(database), SQLiteMockApprovalSubmitter(database)
        checkpoint = SQLiteCheckpointSaver(database)
        fixture = CaptureWorkflowFixture(case.fixture, policy_directory, client)
        turns = []
        record_start, retrieval_start = len(client.records), 0

        async def on_turn(index, turn, result, error, session_id):
            nonlocal record_start, retrieval_start
            turns.append(
                await turn_snapshot(
                    index,
                    turn.query,
                    result,
                    error,
                    session_id,
                    store,
                    submitter,
                    client.records[record_start:],
                    fixture.retrieved_ids[retrieval_start:],
                )
            )
            record_start, retrieval_start = len(client.records), len(fixture.retrieved_ids)
            if on_case is not None:
                on_case(
                    captures
                    + [
                        {
                            "case_id": case.case_id,
                            "execution_id": session_id,
                            "fixture": case.fixture,
                            "split": case.split,
                            "turns": turns,
                            "capture_complete": False,
                        }
                    ],
                    evaluations,
                )

        evaluation = await evaluate_workflow_case(
            case,
            store=store,
            submitter=submitter,
            checkpointer=checkpoint,
            policy_directory=policy_directory,
            runtime_fixture=fixture,
            on_turn=on_turn,
        )
        evaluation["evidence_mode"] = "agent_capture_" + client.provider.evidence_mode
        evaluations.append(evaluation)
        captures.append(
            {
                "case_id": case.case_id,
                "execution_id": evaluation["execution_id"],
                "fixture": case.fixture,
                "split": case.split,
                "turns": turns,
                "capture_complete": True,
            }
        )
        if on_case is not None:
            on_case(captures, evaluations)
    return {
        "results": captures,
        "workflow_results": evaluations,
        "provider_mode": client.provider.evidence_mode,
        "model_version": client.provider.model_version,
        "provider_calls": client.calls,
        "reserved_tokens": client.reserved_tokens,
        "budget": client.budget.model_dump(),
        "human_reviewed_samples": 0,
        "quality_conclusion": None,
    }


def capture_router(store, submitter, checkpoint, policies, client, searcher):
    materials = RequiredMaterialsChecker.from_policy_directory(policies)
    approval = ApprovalRuleChecker.from_policy_directory(policies)
    return AgentRouter(
        intent_classifier=IntentClassifier(llm_client=client),
        policy_answer_service=PolicyAnswerService(retriever=searcher, llm_client=client),
        material_checker=materials,
        approval_checker=approval,
        draft_generator=ApplicationDraftGenerator.from_policy_directory(
            policies,
            material_checker=materials,
            approval_checker=approval,
            user_context=DraftUserContext(
                "EVAL-001",
                "Synthetic",
                "Evaluation",
                ("EMPLOYEE",),
                "中国大陆",
                "trusted_evaluation_context",
            ),
            clock=lambda: datetime(2026, 8, 7, tzinfo=UTC),
        ),
        submission_service=submitter,
        checkpointer=checkpoint,
        state_persister=store,
        tool_executor=ResilientToolExecutor(
            safe_tool_timeout_seconds=client.budget.timeout_seconds + 1,
            max_attempts=1,
        ),
    )


async def capture_cases(cases, client, directory, policy_directory):
    """Rebuild Router each turn; keep isolated DB per case and read state independently."""
    captured = []
    for case in cases:
        session_id = "capture-" + uuid4().hex
        database = Path(directory) / (uuid4().hex + ".db")
        store, submitter = SQLiteAgentStateStore(database), SQLiteMockApprovalSubmitter(database)
        checkpoint = SQLiteCheckpointSaver(database)
        # Reuse synthetic ACL fixtures, but use real IntentClassifier and PolicyAnswerService.
        searcher = WorkflowFixture(
            "mixed_acl" if case.access == "allowed" else "denied", policy_directory
        )
        turns = []
        for index, query in enumerate(case.queries):
            start = len(client.records)
            retrieval_start = len(searcher.retrieved_ids)
            router = capture_router(
                store, submitter, checkpoint, policy_directory, client, searcher
            )
            result, error = None, None
            try:
                result = await router.route(query, session_id=session_id)
            except Exception as exc:
                error = type(exc).__name__
            turns.append(
                await turn_snapshot(
                    index,
                    query,
                    result,
                    error,
                    session_id,
                    store,
                    submitter,
                    client.records[start:],
                    searcher.retrieved_ids[retrieval_start:],
                )
            )
        captured.append(
            {
                "case_id": case.case_id,
                "execution_id": session_id,
                "access": case.access,
                "turns": turns,
            }
        )
    return {
        "provider_mode": client.provider.evidence_mode,
        "model_version": client.provider.model_version,
        "provider_calls": client.calls,
        "reserved_tokens": client.reserved_tokens,
        "budget": client.budget.model_dump(),
        "quality_conclusion": None,
        "human_reviewed_samples": 0,
        "results": captured,
    }
