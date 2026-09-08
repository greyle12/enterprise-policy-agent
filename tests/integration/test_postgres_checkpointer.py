from __future__ import annotations

import os
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import AgentResponseStatus, AgentRouter
from app.persistence import (
    PostgresCheckpointRuntime,
    SQLiteAgentStateStore,
    SQLiteMockApprovalSubmitter,
)
from app.persistence.postgres_schema import PostgresAgentStateSchemaManager
from app.rag.policy_answer_service import PolicyAnswer
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftStatus, DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker

pytestmark = pytest.mark.postgres_integration

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_SESSION_ID = "postgres-checkpoint-hitl-resume"
_USER_CONTEXT = DraftUserContext(
    employee_id="CHECKPOINT-EMP-001",
    employee_name="Checkpoint 测试用户",
    department="研发部",
    roles=("EMPLOYEE",),
    region="中国大陆",
    identity_source="trusted_integration_context",
)
_COMPLETE_PURCHASE = (
    "帮我生成采购申请草稿，采购3台27英寸办公显示器，每台2000元，"
    "采购目的为给新员工配置办公设备，采购类别为IT设备，规格为27英寸2K，"
    "预算编号RD-2026，交付日期2026-09-15，使用地点苏州办公室，"
    "推荐供应商为苏州科技有限公司，推荐理由为历史合作交付稳定，普通采购，"
    "已准备技术需求说明、信息技术评审意见、产品规格说明和2家供应商报价。"
)


class _DeterministicIntentClassifier:
    async def classify(self, user_input: str) -> IntentClassification:
        return IntentClassification(
            intent=(
                IntentType.DRAFT_GENERATION
                if "生成" in user_input and "草稿" in user_input
                else IntentType.UNKNOWN
            ),
            confidence=1.0,
            reason="Phase 38 Step 4 PostgreSQL checkpoint integration test.",
        )


class _StubPolicyAnswerService:
    async def answer(self, question: str) -> PolicyAnswer:
        return PolicyAnswer(question=question, answer="本测试不验证制度问答。", citations=())


def _test_dsn() -> str:
    dsn = os.getenv("AGENT_POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AGENT_POSTGRES_TEST_DSN is not configured")
    database = str(conninfo_to_dict(dsn).get("dbname", ""))
    if not database.endswith("_test"):
        pytest.fail("AGENT_POSTGRES_TEST_DSN database name must end with _test")
    return dsn


@pytest.fixture(scope="session")
def postgres_checkpoint_dsn() -> str:
    dsn = _test_dsn()
    status = PostgresAgentStateSchemaManager.from_dsn(
        dsn,
        connect_timeout_seconds=5.0,
    ).setup()
    assert status.ready
    return dsn


def _build_router(database_path: Path, checkpointer) -> AgentRouter:
    material_checker = RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)
    approval_checker = ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)
    return AgentRouter(
        intent_classifier=_DeterministicIntentClassifier(),
        policy_answer_service=_StubPolicyAnswerService(),
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=ApplicationDraftGenerator.from_policy_directory(
            _POLICY_DIRECTORY,
            material_checker=material_checker,
            approval_checker=approval_checker,
            user_context=_USER_CONTEXT,
        ),
        submission_service=SQLiteMockApprovalSubmitter(database_path),
        checkpointer=checkpointer,
        state_persister=SQLiteAgentStateStore(database_path),
    )


@pytest.mark.asyncio
async def test_instance_b_resumes_instance_a_hitl_checkpoint(
    postgres_checkpoint_dsn: str,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "step4-projections.db"
    instance_a = PostgresCheckpointRuntime(
        postgres_checkpoint_dsn,
        min_pool_size=1,
        max_pool_size=4,
    )
    status = await instance_a.setup()
    assert status.ready
    try:
        await instance_a.checkpointer.adelete_thread(_SESSION_ID)
        created = await _build_router(database_path, instance_a.checkpointer).route(
            _COMPLETE_PURCHASE,
            session_id=_SESSION_ID,
        )
    finally:
        await instance_a.close()

    assert created.status is AgentResponseStatus.AWAITING_CONFIRMATION
    assert created.session is not None
    assert created.session.pending_confirmation is True
    assert created.session.checkpoint_backend == "postgresql"
    assert created.session.survives_process_restart is True

    instance_b = PostgresCheckpointRuntime(
        postgres_checkpoint_dsn,
        min_pool_size=1,
        max_pool_size=4,
    )
    await instance_b.open()
    try:
        confirmed = await _build_router(database_path, instance_b.checkpointer).route(
            "确认草稿",
            session_id=_SESSION_ID,
        )
        restored = await instance_b.checkpointer.aget_tuple(
            {"configurable": {"thread_id": _SESSION_ID}}
        )
        await instance_b.checkpointer.adelete_thread(_SESSION_ID)
        assert (
            await instance_b.checkpointer.aget_tuple({"configurable": {"thread_id": _SESSION_ID}})
            is None
        )
    finally:
        await instance_b.close()

    assert confirmed.status is AgentResponseStatus.CONFIRMED
    assert confirmed.session is not None
    assert confirmed.session.turn_number == 2
    assert confirmed.session.checkpoint_backend == "postgresql"
    assert confirmed.application_draft is not None
    assert confirmed.application_draft.draft is not None
    assert confirmed.application_draft.draft.status is DraftStatus.CONFIRMED
    assert restored is not None
