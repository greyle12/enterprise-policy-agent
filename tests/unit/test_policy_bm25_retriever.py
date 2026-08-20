from __future__ import annotations

from datetime import date
from pathlib import Path

from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_retriever import PolicyRetriever, RetrievalMethod
from app.schemas.policy import SecurityLevel
from app.security import PolicyAccessContext, TrustedIdentitySource

POLICY_DIRECTORY = Path("data/policies")


class _TrackingEmbeddingProvider:
    def __init__(self) -> None:
        self.query_inputs: list[str] = []

    @property
    def dimension(self) -> int:
        return 1

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_inputs.append(text)
        return [1.0]


def _access_context() -> PolicyAccessContext:
    return PolicyAccessContext(
        employee_id="BM25-TEST-001",
        department="技术部",
        roles=("EMPLOYEE",),
        security_clearance=SecurityLevel.INTERNAL,
        region="中国大陆",
        identity_source=TrustedIdentitySource.TEST_FIXTURE,
    )


def test_existing_policy_retriever_builds_keyword_index_from_retrieval_text() -> None:
    chunks = chunk_policy_directory(POLICY_DIRECTORY)
    provider = _TrackingEmbeddingProvider()
    retriever = PolicyRetriever(embedding_provider=provider, chunks=chunks)

    results = retriever.search_keywords("住宿发票", top_k=3)

    assert retriever.keyword_size == len(chunks) == 199
    assert results
    assert results[0].chunk.document_id == "TRAVEL_POLICY_001"
    assert results[0].retrieval_method is RetrievalMethod.BM25
    assert results[0].score > 0.0
    assert provider.query_inputs == []


def test_keyword_search_finds_exact_document_identifier() -> None:
    chunks = chunk_policy_directory(POLICY_DIRECTORY)
    retriever = PolicyRetriever(
        embedding_provider=_TrackingEmbeddingProvider(),
        chunks=chunks,
    )

    results = retriever.search_keywords("INFORMATION_SECURITY_POLICY_001", top_k=5)

    assert results
    assert all(result.chunk.document_id == "INFORMATION_SECURITY_POLICY_001" for result in results)


def test_restricted_keyword_search_excludes_unauthorized_before_scoring() -> None:
    base_chunk = chunk_policy_directory(POLICY_DIRECTORY)[0]
    unauthorized = base_chunk.model_copy(
        update={
            "chunk_id": "unauthorized-core",
            "retrieval_text": "核心机密项目代号 BLACK-ORCHID",
            "security_level": SecurityLevel.CORE,
        }
    )
    authorized = base_chunk.model_copy(
        update={
            "chunk_id": "authorized-internal",
            "retrieval_text": "普通内部差旅报销规定",
            "security_level": SecurityLevel.INTERNAL,
        }
    )
    retriever = PolicyRetriever(
        embedding_provider=_TrackingEmbeddingProvider(),
        chunks=[unauthorized, authorized],
    )
    restricted = retriever.restrict(_access_context(), as_of_date=date(2026, 8, 20))

    raw_results = retriever.search_keywords("BLACK-ORCHID")
    restricted_results = restricted.search_keywords("BLACK-ORCHID")

    assert raw_results[0].chunk.chunk_id == "unauthorized-core"
    assert restricted.allowed_chunk_count == 1
    assert restricted_results == []


def test_vector_search_contract_remains_available_after_bm25_indexing() -> None:
    chunks = chunk_policy_directory(POLICY_DIRECTORY)[:2]
    provider = _TrackingEmbeddingProvider()
    retriever = PolicyRetriever(embedding_provider=provider, chunks=chunks)

    results = retriever.search("原有向量查询", top_k=1)

    assert len(results) == 1
    assert results[0].retrieval_method is RetrievalMethod.VECTOR
    assert provider.query_inputs == ["原有向量查询"]
