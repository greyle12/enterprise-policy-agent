from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_retriever import PolicyRetriever, RetrievalMethod
from app.schemas.chunk import PolicyChunk
from app.schemas.policy import SecurityLevel
from app.security import PolicyAccessContext, TrustedIdentitySource

POLICY_DIRECTORY = Path("data/policies")


class _HybridEmbeddingProvider:
    def __init__(self) -> None:
        self.query_inputs: list[str] = []

    @property
    def dimension(self) -> int:
        return 2

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        del texts
        return [
            [0.8, 0.2],
            [0.0, 1.0],
            [1.0, 0.0],
        ]

    def embed_query(self, text: str) -> list[float]:
        self.query_inputs.append(text)
        return [1.0, 0.0]


def _hybrid_chunks() -> list[PolicyChunk]:
    base_chunks = chunk_policy_directory(POLICY_DIRECTORY)[:3]
    retrieval_texts = (
        "语义相关但没有精确编号",
        "制度编号 EXP-900 精确词面候选",
        "语义相关并包含制度编号 EXP-900",
    )
    return [
        chunk.model_copy(
            update={
                "chunk_id": f"hybrid-{index}",
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
        employee_id="HYBRID-TEST-001",
        department="技术部",
        roles=("EMPLOYEE",),
        security_clearance=SecurityLevel.INTERNAL,
        region="中国大陆",
        identity_source=TrustedIdentitySource.TEST_FIXTURE,
    )


def test_hybrid_search_fuses_vector_and_bm25_with_rrf() -> None:
    chunks = _hybrid_chunks()
    provider = _HybridEmbeddingProvider()
    retriever = PolicyRetriever(embedding_provider=provider, chunks=chunks)

    results = retriever.search_hybrid("EXP-900", top_k=3, candidate_k=3)

    assert results[0].chunk.chunk_id == "hybrid-3"
    assert results[0].retrieval_method is RetrievalMethod.HYBRID
    assert {signal.method for signal in results[0].retrieval_signals} == {
        RetrievalMethod.VECTOR,
        RetrievalMethod.BM25,
    }
    assert results[0].score == pytest.approx(
        sum(signal.rrf_contribution for signal in results[0].retrieval_signals)
    )
    assert provider.query_inputs == ["EXP-900"]


def test_hybrid_search_preserves_single_channel_candidates() -> None:
    retriever = PolicyRetriever(
        embedding_provider=_HybridEmbeddingProvider(),
        chunks=_hybrid_chunks(),
    )

    results = retriever.search_hybrid("火星天气预报", top_k=2, candidate_k=2)

    assert len(results) == 2
    assert all(result.retrieval_method is RetrievalMethod.HYBRID for result in results)
    assert all(
        [signal.method for signal in result.retrieval_signals] == [RetrievalMethod.VECTOR]
        for result in results
    )


def test_hybrid_search_falls_back_to_vector_for_lexically_unsearchable_query() -> None:
    retriever = PolicyRetriever(
        embedding_provider=_HybridEmbeddingProvider(),
        chunks=_hybrid_chunks(),
    )

    results = retriever.search_hybrid("!!!", top_k=1)

    assert len(results) == 1
    assert [signal.method for signal in results[0].retrieval_signals] == [RetrievalMethod.VECTOR]


def test_restricted_hybrid_search_filters_both_channels_before_fusion() -> None:
    chunks = _hybrid_chunks()
    chunks[2] = chunks[2].model_copy(update={"security_level": SecurityLevel.CORE})
    retriever = PolicyRetriever(
        embedding_provider=_HybridEmbeddingProvider(),
        chunks=chunks,
    ).restrict(_access_context(), as_of_date=date(2026, 8, 20))

    results = retriever.search_hybrid("EXP-900", top_k=2, candidate_k=2)

    assert retriever.allowed_chunk_count == 2
    assert results
    assert all(result.chunk.chunk_id != "hybrid-3" for result in results)
    assert all(result.chunk.security_level is SecurityLevel.INTERNAL for result in results)


@pytest.mark.parametrize(
    ("top_k", "candidate_k", "message"),
    [
        (0, None, "top_k"),
        (3, 2, "candidate_k"),
        (1, 1.5, "candidate_k"),
    ],
)
def test_hybrid_search_validates_limits(
    top_k: int,
    candidate_k: int | float | None,
    message: str,
) -> None:
    retriever = PolicyRetriever(
        embedding_provider=_HybridEmbeddingProvider(),
        chunks=_hybrid_chunks(),
    )

    with pytest.raises(ValueError, match=message):
        retriever.search_hybrid(
            "EXP-900",
            top_k=top_k,
            candidate_k=candidate_k,  # type: ignore[arg-type]
        )
