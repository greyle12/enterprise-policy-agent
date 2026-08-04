from pathlib import Path

import pytest

from app.rag.policy_chunker import (
    chunk_policy_directory,
)
from app.rag.policy_context import (
    build_policy_context,
)
from app.rag.policy_retriever import (
    PolicyRetrievalResult,
)

POLICY_DIRECTORY = Path("data/policies")


@pytest.fixture
def sample_results() -> list[PolicyRetrievalResult]:
    chunks = chunk_policy_directory(
        POLICY_DIRECTORY
    )[:3]

    return [
        PolicyRetrievalResult(
            chunk=chunk,
            score=1.0 - index * 0.1,
        )
        for index, chunk in enumerate(chunks)
    ]


def test_returns_empty_context_for_no_results() -> None:
    context = build_policy_context([])

    assert context.text == ""
    assert context.citations == ()


def test_builds_numbered_context_blocks(
    sample_results: list[PolicyRetrievalResult],
) -> None:
    context = build_policy_context(sample_results)

    assert "[S1]" in context.text
    assert "[S2]" in context.text
    assert "[S3]" in context.text

    for result in sample_results:
        assert result.chunk.document_title in context.text
        assert result.chunk.content in context.text


def test_builds_structured_citations(
    sample_results: list[PolicyRetrievalResult],
) -> None:
    context = build_policy_context(sample_results)

    citation = context.citations[0]
    chunk = sample_results[0].chunk

    assert citation.source_id == "S1"
    assert citation.chunk_id == chunk.chunk_id
    assert citation.document_title == (
        chunk.document_title
    )
    assert citation.article_label == (
        chunk.article_label
    )
    assert citation.score == pytest.approx(1.0)


def test_preserves_retrieval_order(
    sample_results: list[PolicyRetrievalResult],
) -> None:
    context = build_policy_context(sample_results)

    assert [
        citation.chunk_id
        for citation in context.citations
    ] == [
        result.chunk.chunk_id
        for result in sample_results
    ]


def test_respects_max_chunks(
    sample_results: list[PolicyRetrievalResult],
) -> None:
    context = build_policy_context(
        sample_results,
        max_chunks=2,
    )

    assert len(context.citations) == 2
    assert "[S1]" in context.text
    assert "[S2]" in context.text
    assert "[S3]" not in context.text


def test_removes_duplicate_chunks(
    sample_results: list[PolicyRetrievalResult],
) -> None:
    duplicated_results = [
        sample_results[0],
        sample_results[0],
        sample_results[1],
    ]

    context = build_policy_context(
        duplicated_results
    )

    assert [
        citation.source_id
        for citation in context.citations
    ] == ["S1", "S2"]

    assert [
        citation.chunk_id
        for citation in context.citations
    ] == [
        sample_results[0].chunk.chunk_id,
        sample_results[1].chunk.chunk_id,
    ]


@pytest.mark.parametrize("max_chunks", [0, -1])
def test_rejects_invalid_max_chunks(
    max_chunks: int,
) -> None:
    with pytest.raises(
        ValueError,
        match="max_chunks",
    ):
        build_policy_context(
            [],
            max_chunks=max_chunks,
        )