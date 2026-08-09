from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.agent.intent_classifier import IntentClassifier
from app.agent.router import AgentRouter
from app.api.routes.agent_messages import (
    router as agent_messages_router,
)
from app.api.routes.policy_answers import (
    router as policy_answers_router,
)
from app.core.config import get_settings
from app.llm.openai_compatible_client import (
    OpenAICompatibleLLMClient,
)
from app.persistence import (
    SQLiteAgentStateStore,
    SQLiteCheckpointSaver,
    SQLiteMockApprovalSubmitter,
)
from app.rag.embeddings import BGEEmbeddingProvider
from app.rag.policy_answer_service import (
    PolicyAnswerService,
)
from app.rag.policy_retriever import PolicyRetriever
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_EMBEDDING_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
_DEMO_DRAFT_USER_CONTEXT = DraftUserContext(
    employee_id="DEMO-EMP-001",
    employee_name="演示用户",
    department="演示部门",
    roles=("EMPLOYEE",),
    region="中国大陆",
    identity_source="trusted_demo_context",
)


def _build_policy_answer_service() -> tuple[
    PolicyAnswerService,
    OpenAICompatibleLLMClient,
]:
    """创建真实制度问答服务及其 LLM 客户端。"""

    embedding_provider = BGEEmbeddingProvider(
        model_name=_EMBEDDING_MODEL_NAME,
    )
    retriever = PolicyRetriever.from_directory(
        _POLICY_DIRECTORY,
        embedding_provider=embedding_provider,
    )

    settings = get_settings()
    llm_client = (
        OpenAICompatibleLLMClient.from_settings(
            settings
        )
    )

    service = PolicyAnswerService(
        retriever=retriever,
        llm_client=llm_client,
    )

    return service, llm_client


@asynccontextmanager
async def _lifespan(
    application: FastAPI,
) -> AsyncIterator[None]:
    """初始化并释放应用级共享资源。"""

    service, llm_client = (
        _build_policy_answer_service()
    )
    material_checker = (
        RequiredMaterialsChecker.from_policy_directory(
            _POLICY_DIRECTORY
        )
    )
    approval_checker = (
        ApprovalRuleChecker.from_policy_directory(
            _POLICY_DIRECTORY
        )
    )
    settings = get_settings()
    state_store = SQLiteAgentStateStore(
        settings.sqlite_database_path
    )
    agent_router = AgentRouter(
        intent_classifier=IntentClassifier(
            llm_client=llm_client,
        ),
        policy_answer_service=service,
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=(
            ApplicationDraftGenerator.from_policy_directory(
                _POLICY_DIRECTORY,
                material_checker=material_checker,
                approval_checker=approval_checker,
                user_context=_DEMO_DRAFT_USER_CONTEXT,
            )
        ),
        submission_service=SQLiteMockApprovalSubmitter(
            settings.sqlite_database_path
        ),
        checkpointer=SQLiteCheckpointSaver(
            settings.sqlite_database_path
        ),
        state_persister=state_store,
    )
    application.state.policy_answer_service = service
    application.state.agent_router = agent_router
    application.state.agent_state_store = state_store

    try:
        yield
    finally:
        await llm_client.close()
        del application.state.agent_state_store
        del application.state.agent_router
        del application.state.policy_answer_service


def create_app(
    *,
    enable_lifespan: bool = True,
) -> FastAPI:
    """创建 FastAPI 应用。"""

    application = FastAPI(
        title="Enterprise Policy Agent",
        version="0.1.0",
        lifespan=(
            _lifespan
            if enable_lifespan
            else None
        ),
    )
    application.include_router(
        policy_answers_router,
        prefix="/api/v1",
    )
    application.include_router(
        agent_messages_router,
        prefix="/api/v1",
    )

    return application


app = create_app()
