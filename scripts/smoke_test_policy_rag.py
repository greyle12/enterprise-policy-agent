"""执行一次真实的制度 RAG 端到端测试。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.core.config import get_settings
from app.llm.openai_compatible_client import (
    OpenAICompatibleLLMClient,
)
from app.rag.embeddings import BGEEmbeddingProvider
from app.rag.policy_answer_service import (
    PolicyAnswerService,
)
from app.rag.policy_retriever import PolicyRetriever

_LOGGER = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_QUESTION = "出差住宿标准是多少？"


async def main() -> None:
    """检索本地制度并生成带来源引用的回答。"""

    embedding_provider = BGEEmbeddingProvider(
        model_name="BAAI/bge-small-zh-v1.5",
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

    try:
        result = await service.answer(_QUESTION)
    finally:
        await llm_client.close()

    citation_ids = ", ".join(
        citation.source_id
        for citation in result.citations
    )

    _LOGGER.info("Question: %s", _QUESTION)
    _LOGGER.info("Answer:\n%s", result.answer)
    _LOGGER.info(
        "Citations: %s",
        citation_ids or "(none)",
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )
    asyncio.run(main())