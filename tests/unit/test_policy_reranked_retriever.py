from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest

from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_retriever import PolicyRetriever, RetrievalMethod
from app.rag.reranking import RerankingProvider
from app.schemas.chunk import PolicyChunk
from app.schemas.policy import SecurityLevel
from app.security import PolicyAccessContext, TrustedIdentitySource

POLICY_DIRECTORY = Path("data/policies")


class _HybridEmbeddingProvider:
    @property
    def dimension(self) -> int:
        return 2

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if len(texts) != 3:
            raise AssertionError("test fixture expects three chunks")
        return [
            [0.8, 0.2],
            [0.0, 1.0],
            [1.0, 0.0],
        ]

    def embed_query(self, text: str) -> list[float]:
        del text
        return [1.0, 0.0]


class _TrackingReranker(RerankingProvider):
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        document_list = list(documents)
        self.calls.append((query, document_list))
        return [1.0 if "精确词面候选" in document else 0.2 for document in document_list]


def _chunks() -> list[PolicyChunk]:
    base_chunks = chunk_policy_directory(POLICY_DIRECTORY)[:3]
    retrieval_texts = (
        "语义相关但没有精确编号",
        "制度编号 EXP-900 精确词面候选",
        "语义相关并包含制度编号 EXP-900",
    )
    return [
        chunk.model_copy(
            update={
                "chunk_id": f"rerank-{index}",
                "retrieval_text": retrieval_text,
            }
        )
        for index, (chunk, retrieval_text) in enumerate(
            zip(base_chunks, retrieval_texts, strict=True),
            start=1,
        )
    ]


def _access_context() -> PolicyAccessContext:
    return PolicyAccessContext(
        employee_id="RERANK-TEST-001",
        department="技术部",
        roles=("EMPLOYEE",),
        security_clearance=SecurityLevel.INTERNAL,
        region="中国大陆",
        identity_source=TrustedIdentitySource.TEST_FIXTURE,
    )


def test_reranker_scores_rrf_candidates_once_and_changes_final_order() -> None:
    provider = _TrackingReranker()
    retriever = PolicyRetriever(
        embedding_provider=_HybridEmbeddingProvider(),
        chunks=_chunks(),
        reranking_provider=provider,
        rerank_candidate_k=3,
    )
    hybrid_results = retriever.search_hybrid("EXP-900", top_k=3, candidate_k=3)

    results = retriever.search_reranked("EXP-900", top_k=2)

    assert retriever.reranker_enabled is True
    assert hybrid_results[0].chunk.chunk_id != "rerank-2"
    assert results[0].chunk.chunk_id == "rerank-2"
    assert results[0].retrieval_method is RetrievalMethod.RERANKED
    assert results[0].score == 1.0
    assert results[0].pre_rerank_rank == 2
    assert results[0].pre_rerank_score == hybrid_results[1].score
    assert results[0].retrieval_signals == hybrid_results[1].retrieval_signals
    assert len(provider.calls) == 1
    assert provider.calls[0][0] == "EXP-900"
    assert len(provider.calls[0][1]) == 3


def test_disabled_reranker_transparently_keeps_rrf_results() -> None:
    retriever = PolicyRetriever(
        embedding_provider=_HybridEmbeddingProvider(),
        chunks=_chunks(),
    )

    expected = retriever.search_hybrid("EXP-900", top_k=2, candidate_k=3)
    actual = retriever.search_reranked("EXP-900", top_k=2, candidate_k=3)

    assert retriever.reranker_enabled is False
    assert actual == expected
    assert all(result.retrieval_method is RetrievalMethod.HYBRID for result in actual)
    assert all(result.pre_rerank_score is None for result in actual)


def test_authorization_filters_candidates_before_reranker_provider() -> None:
    chunks = _chunks()
    secret = "CORE-RERANK-SECRET"
    chunks[2] = chunks[2].model_copy(
        update={
            "retrieval_text": f"{chunks[2].retrieval_text} {secret}",
            "security_level": SecurityLevel.CORE,
        }
    )
    provider = _TrackingReranker()
    retriever = PolicyRetriever(
        embedding_provider=_HybridEmbeddingProvider(),
        chunks=chunks,
        reranking_provider=provider,
        rerank_candidate_k=3,
    ).restrict(_access_context(), as_of_date=date(2026, 8, 20))

    results = retriever.search_reranked("EXP-900", top_k=2)

    assert retriever.allowed_chunk_count == 2
    assert results
    assert all(result.chunk.chunk_id != "rerank-3" for result in results)
    assert len(provider.calls) == 1
    assert all(secret not in document for document in provider.calls[0][1])


def test_empty_authorized_pool_does_not_call_reranker() -> None:
    provider = _TrackingReranker()
    retriever = PolicyRetriever(
        embedding_provider=_HybridEmbeddingProvider(),
        chunks=_chunks(),
        reranking_provider=provider,
        rerank_candidate_k=3,
    )

    results = retriever.search_reranked(
        "EXP-900",
        top_k=2,
        allowed_chunk_ids=frozenset(),
    )

    assert results == []
    assert provider.calls == []


def test_reranked_search_validates_candidate_window() -> None:
    retriever = PolicyRetriever(
        embedding_provider=_HybridEmbeddingProvider(),
        chunks=_chunks(),
        reranking_provider=_TrackingReranker(),
    )

    with pytest.raises(ValueError, match="candidate_k"):
        retriever.search_reranked("EXP-900", top_k=3, candidate_k=2)
    with pytest.raises(ValueError, match="rerank_candidate_k"):
        PolicyRetriever(
            embedding_provider=_HybridEmbeddingProvider(),
            chunks=_chunks(),
            rerank_candidate_k=0,
        )
