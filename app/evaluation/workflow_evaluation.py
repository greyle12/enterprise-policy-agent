"""Dataset-driven deterministic workflow evaluation using isolated repositories."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field

from app.agent.router import AgentRouter
from app.evaluation.models import EvaluationAssertion, _StrictModel
from app.evaluation.runtime import OfflineIntentClassifier, _OfflinePolicyAnswerer
from app.persistence import (
    SQLiteAgentStateStore,
    SQLiteCheckpointSaver,
    SQLiteMockApprovalSubmitter,
)
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker
from app.evaluation.workflow_fixtures import WorkflowFixture
from app.rag.policy_answer_service import _NO_EVIDENCE_ANSWER


class WorkflowTurn(_StrictModel):
    query: str = Field(min_length=1)
    status: str
    phase: str
    submissions: int = Field(ge=0)
    intent: str | None = None
    nodes: tuple[str, ...] | None = None
    tools: tuple[str, ...] | None = None
    fields: dict[
        Literal[
            "quantity",
            "estimated_unit_price",
            "estimated_total_amount",
            "calculated_total_amount",
            "total_reimbursement_amount",
            "leave_days",
            "start_date",
            "end_date",
        ],
        str | int | None,
    ] = Field(default_factory=dict)
    error: Literal["PromptInjectionBlockedError"] | None = None
    authorization_violation: bool | None = None
    recovered: bool | None = None
    llm_calls: int | None = Field(default=None, ge=0)
    abstained: bool | None = None
    citation_sources: tuple[str, ...] | None = None
    tool_attempts: tuple[int, ...] | None = None
    reconciled: bool | None = None


class WorkflowCase(_StrictModel):
    case_id: str = Field(pattern=r"^WF-[0-9]{3}$")
    scenario: str
    dataset_version: Literal[
        "workflow-v1", "workflow-challenge-v1", "workflow-safety-v1", "workflow-regression-v1"
    ] = "workflow-v1"
    split: Literal["golden", "safety", "challenge", "regression", "holdout"]
    principal: Literal["synthetic_employee"] = "synthetic_employee"
    tags: tuple[str, ...] = ()
    fixture: Literal[
        "business",
        "denied",
        "mixed_acl",
        "expired",
        "no_evidence",
        "injected_evidence",
        "transient_intent",
        "timeout_policy",
        "mutation_ack_loss",
    ] = "business"
    turns: tuple[WorkflowTurn, ...] = Field(min_length=1)


def load_workflow_cases(path):
    cases = [
        WorkflowCase.model_validate_json(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not cases or len({c.case_id for c in cases}) != len(cases):
        raise ValueError("empty dataset or duplicate case ID")
    return cases


def build_workflow_router(store, submitter, checkpointer, policy_directory, fixture=None):
    materials = RequiredMaterialsChecker.from_policy_directory(policy_directory)
    approval = ApprovalRuleChecker.from_policy_directory(policy_directory)
    user = DraftUserContext(
        "EVAL-001",
        "Synthetic",
        "Evaluation",
        ("EMPLOYEE",),
        "中国大陆",
        "trusted_evaluation_context",
    )
    return AgentRouter(
        intent_classifier=fixture if fixture else OfflineIntentClassifier(),
        policy_answer_service=fixture.answerer if fixture else _OfflinePolicyAnswerer(),
        tool_executor=fixture.executor if fixture else None,
        material_checker=materials,
        approval_checker=approval,
        draft_generator=ApplicationDraftGenerator.from_policy_directory(
            policy_directory,
            material_checker=materials,
            approval_checker=approval,
            user_context=user,
            clock=lambda: datetime(2026, 8, 7, tzinfo=UTC),
        ),
        submission_service=submitter,
        checkpointer=checkpointer,
        state_persister=store,
    )


class RecordingSubmitter:
    """Observe actual submit arguments locally without changing the delegated operation."""

    def __init__(self, delegate, session_id, fixture=None):
        self.delegate = delegate
        self.session_id = session_id
        self.arguments_valid = []
        self.fixture = fixture

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def submit(self, draft, **kwargs):
        self.arguments_valid.append(
            bool(
                draft.user_confirmed
                and kwargs["session_id"] == self.session_id
                and kwargs["user_context"].employee_id == draft.applicant.employee_id == "EVAL-001"
                and kwargs["confirmation_text"].strip()
                and kwargs["submission_idempotency_key"]
            )
        )
        if self.fixture:
            return await self.fixture.submit(self.delegate, draft, **kwargs)
        return await self.delegate.submit(draft, **kwargs)


async def evaluate_workflow_case(
    case, *, store, submitter, checkpointer, policy_directory, runtime_fixture=None, on_turn=None
):
    """Rebuild router each turn, then read persisted state independently of reply text."""
    session_id = "eval-" + uuid4().hex
    fixture = runtime_fixture or (
        WorkflowFixture(case.fixture, policy_directory) if case.fixture != "business" else None
    )
    submitter = RecordingSubmitter(submitter, session_id, fixture)
    assertions = []
    observations = []

    def check(name, expected, actual):
        assertions.append(
            EvaluationAssertion(
                name=name, expected=expected, actual=actual, passed=expected == actual
            )
        )

    for index, turn in enumerate(case.turns):
        prefix = f"turn_{index}."
        router = build_workflow_router(store, submitter, checkpointer, policy_directory, fixture)
        try:
            result = await router.route(turn.query, session_id=session_id)
            error = None
        except Exception as exc:
            result = None
            error = type(exc).__name__
        check(prefix + "error", turn.error, error)
        persisted = await store.get_session(session_id)
        records = await submitter.list_audit_records()
        records = [r for r in records if r.session_id == session_id]
        created = [r for r in records if r.event.value == "submitted"]
        if on_turn is not None:
            await on_turn(index, turn, result, error, session_id)
        check(prefix + "side_effect_count", turn.submissions, len(created))
        if submitter.arguments_valid:
            check(prefix + "tool_arguments", True, all(submitter.arguments_valid))
        check(
            prefix + "duplicate_side_effect",
            True,
            len(created) == len({r.submission_idempotency_key for r in created}),
        )
        if result is None:
            if turn.error is not None:
                check(prefix + "blocked_state_unchanged", True, persisted is None)
            if turn.error is None:
                check(prefix + "result_present", True, False)
            continue
        check(prefix + "status", turn.status, result.status.value)
        check(prefix + "persisted_phase", turn.phase, persisted.phase.value if persisted else None)
        if turn.authorization_violation is not None:
            check(
                prefix + "authorization_violation",
                turn.authorization_violation,
                fixture.authorization_violation(result.reply) if fixture else None,
            )
        if turn.recovered is not None:
            check(
                prefix + "recovery",
                turn.recovered,
                result.resilience.recovered if result.resilience else None,
            )
        if turn.llm_calls is not None:
            check(prefix + "llm_calls", turn.llm_calls, len(fixture.messages) if fixture else None)
        if turn.abstained is not None:
            check(prefix + "abstention", turn.abstained, result.reply == _NO_EVIDENCE_ANSWER)
        if turn.citation_sources is not None:
            check(
                prefix + "citation_sources",
                list(turn.citation_sources),
                [c.source_id for c in result.citations],
            )
        nodes = [s.node.value for s in result.workflow.steps] if result.workflow else []
        tools = [c.tool.value for c in result.resilience.tool_calls] if result.resilience else []
        attempts = [c.attempts for c in result.resilience.tool_calls] if result.resilience else []
        if turn.tool_attempts is not None:
            check(prefix + "tool_attempts", list(turn.tool_attempts), attempts)
        if turn.reconciled is not None:
            check(
                prefix + "reconciled",
                turn.reconciled,
                bool(
                    result.submission
                    and result.submission.duplicate_submission
                    and len(created) == 1
                    and result.status.value == "submitted"
                ),
            )
        if turn.intent is not None:
            check(prefix + "intent", turn.intent, result.classification.intent.value)
        if turn.nodes is not None:
            check(prefix + "trajectory", list(turn.nodes), nodes)
        if turn.tools is not None:
            check(prefix + "tool_selection", list(turn.tools), tools)
        draft_result = (
            await store.get_draft(persisted.active_draft_id)
            if (persisted and persisted.active_draft_id)
            else None
        )
        draft = draft_result.draft if draft_result else None
        if draft:
            expected_statuses = {
                "collecting_information": {"waiting_for_information", "waiting_for_materials"},
                "awaiting_confirmation": {"waiting_for_confirmation"},
                "confirmed": {"confirmed"},
                "submitted": {"submitted"},
                "cancelled": {"cancelled"},
            }
            check(
                prefix + "draft_lifecycle",
                True,
                draft.status.value in expected_statuses.get(turn.phase, set()),
            )
            check(prefix + "draft_submitted_flag", turn.phase == "submitted", draft.submitted)
        values = {f.field_name: str(f.value) for f in draft.fields} if draft else {}
        for key, value in turn.fields.items():
            check(prefix + "field." + key, None if value is None else str(value), values.get(key))
        if created:
            check(
                prefix + "confirmation_compliance",
                True,
                bool(draft and draft.user_confirmed),
            )
            stored_submission = await submitter.get_submission(draft_id=created[0].draft_id)
            check(prefix + "submission_persisted", True, stored_submission is not None)
            if draft and draft.submitted and stored_submission:
                check(
                    prefix + "submission_identity",
                    draft.submission_id,
                    stored_submission.submission_result.submission_id,
                )
        observations.append(
            {
                "turn": index,
                "nodes": nodes,
                "tools": tools,
                "attempts": attempts,
                "status": result.status.value,
                "submissions": len(created),
            }
        )
    return {
        "case_id": case.case_id,
        "scenario": case.scenario,
        "split": case.split,
        "passed": all(a.passed for a in assertions),
        "assertions": [a.model_dump(mode="json") for a in assertions],
        "observations": observations,
        "evidence_mode": "deterministic_integration",
        "execution_id": session_id,
    }


async def run_sqlite_cases(cases, directory, policy_directory):
    results = []
    for case in cases:
        path = Path(directory) / (uuid4().hex + ".db")
        results.append(
            await evaluate_workflow_case(
                case,
                store=SQLiteAgentStateStore(path),
                submitter=SQLiteMockApprovalSubmitter(path),
                checkpointer=SQLiteCheckpointSaver(path),
                policy_directory=policy_directory,
            )
        )
    return results
