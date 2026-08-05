from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api.routes.policy_answers import (
    router as policy_answers_router,
)
from app.core.config import get_settings
from app.llm.openai_compatible_client import (
    OpenAICompatibleLLMClient,
)
from app.rag.embeddings import BGEEmbeddingProvider
from app.rag.policy_answer_service import (
    PolicyAnswerService,
)
from app.rag.policy_retriever import PolicyRetriever

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_EMBEDDING_MODEL_NAME = "BAAI/bge-small-zh-v1.5"


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
    application.state.policy_answer_service = service

    try:
        yield
    finally:
        await llm_client.close()
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

    return application


app = create_app()