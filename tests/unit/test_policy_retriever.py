from pathlib import Path

import pytest

from app.rag.policy_chunker import (
    chunk_policy_directory,
)
from app.rag.policy_retriever import PolicyRetriever
from app.schemas.chunk import PolicyChunk

POLICY_DIRECTORY = Path("data/policies")


class FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.document_inputs: list[str] = []
        self.query_inputs: list[str] = []

    @property
    def dimension(self) -> int:
        return 2

    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        self.document_inputs = list(texts)

        return [
            [1.0, 0.0]
            if index == 0
            else [0.0, 1.0]
            for index, _ in enumerate(texts)
        ]

    def embed_query(
        self,
        text: str,
    ) -> list[float]:
        self.query_inputs.append(text)

        if text == "first":
            return [1.0, 0.0]

        return [0.0, 1.0]


class IncompleteEmbeddingProvider(
    FakeEmbeddingProvider
):
    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        self.document_inputs = list(texts)
        return [[1.0, 0.0]]


@pytest.fixture
def sample_chunks() -> list[PolicyChunk]:
    chunks = chunk_policy_directory(
        POLICY_DIRECTORY
    )
    return chunks[:2]


def test_builds_index_from_retrieval_text(
    sample_chunks: list[PolicyChunk],
) -> None:
    provider = FakeEmbeddingProvider()

    retriever = PolicyRetriever(
        embedding_provider=provider,
        chunks=sample_chunks,
    )

    assert retriever.size == 2
    assert retriever.dimension == 2
    assert provider.document_inputs == [
        chunk.retrieval_text
        for chunk in sample_chunks
    ]


def test_search_returns_matching_policy_chunk(
    sample_chunks: list[PolicyChunk],
) -> None:
    provider = FakeEmbeddingProvider()
    retriever = PolicyRetriever(
        embedding_provider=provider,
        chunks=sample_chunks,
    )

    results = retriever.search(
        "second",
        top_k=2,
    )

    assert results[0].chunk.chunk_id == (
        sample_chunks[1].chunk_id
    )
    assert results[0].score == pytest.approx(1.0)
    assert provider.query_inputs == ["second"]


def test_search_respects_top_k(
    sample_chunks: list[PolicyChunk],
) -> None:
    retriever = PolicyRetriever(
        embedding_provider=FakeEmbeddingProvider(),
        chunks=sample_chunks,
    )

    results = retriever.search(
        "first",
        top_k=1,
    )

    assert len(results) == 1
    assert results[0].chunk.chunk_id == (
        sample_chunks[0].chunk_id
    )


@pytest.mark.parametrize(
    "query",
    ["", "   ", "\n"],
)
def test_search_rejects_blank_query(
    query: str,
    sample_chunks: list[PolicyChunk],
) -> None:
    retriever = PolicyRetriever(
        embedding_provider=FakeEmbeddingProvider(),
        chunks=sample_chunks,
    )

    with pytest.raises(
        ValueError,
        match="query must not be blank",
    ):
        retriever.search(query)


def test_search_rejects_invalid_top_k(
    sample_chunks: list[PolicyChunk],
) -> None:
    retriever = PolicyRetriever(
        embedding_provider=FakeEmbeddingProvider(),
        chunks=sample_chunks,
    )

    with pytest.raises(
        ValueError,
        match="top_k",
    ):
        retriever.search("first", top_k=0)


def test_rejects_duplicate_chunk_ids(
    sample_chunks: list[PolicyChunk],
) -> None:
    duplicate_chunks = [
        sample_chunks[0],
        sample_chunks[0],
    ]

    with pytest.raises(
        ValueError,
        match="chunk_id",
    ):
        PolicyRetriever(
            embedding_provider=(
                FakeEmbeddingProvider()
            ),
            chunks=duplicate_chunks,
        )


def test_rejects_mismatched_embedding_count(
    sample_chunks: list[PolicyChunk],
) -> None:
    with pytest.raises(
        RuntimeError,
        match="Embedding count",
    ):
        PolicyRetriever(
            embedding_provider=(
                IncompleteEmbeddingProvider()
            ),
            chunks=sample_chunks,
        )