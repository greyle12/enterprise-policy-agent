from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from app.rag.indexing import (
    INDEX_FINGERPRINT_METADATA_KEY,
    DocumentIndexingStatus,
    PolicyDocumentIndexer,
)
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_retriever import PolicyRetriever
from app.rag.vector_index import InMemoryVectorIndex
from app.schemas.chunk import PolicyChunk

POLICY_DIRECTORY = Path("data/policies")


class TrackingEmbeddingProvider:
    def __init__(self, *, incomplete: bool = False) -> None:
        self.document_batches: list[list[str]] = []
        self.query_inputs: list[str] = []
        self._incomplete = incomplete

    @property
    def dimension(self) -> int:
        return 2

    def embed_documents(self, texts) -> list[list[float]]:
        batch = list(texts)
        self.document_batches.append(batch)
        vectors = [[1.0, 0.0] if index % 2 == 0 else [0.0, 1.0] for index, _ in enumerate(batch)]
        return vectors[:-1] if self._incomplete and vectors else vectors

    def embed_query(self, text: str) -> list[float]:
        self.query_inputs.append(text)
        return [1.0, 0.0]


@pytest.fixture
def chunks_from_two_documents() -> list[PolicyChunk]:
    chunks = chunk_policy_directory(POLICY_DIRECTORY)
    document_ids: list[str] = []
    selected: list[PolicyChunk] = []
    for chunk in chunks:
        if chunk.document_id not in document_ids:
            document_ids.append(chunk.document_id)
        if len(document_ids) <= 2:
            selected.append(chunk)
        if len(document_ids) > 2:
            break
    return selected


def _indexer(
    provider: TrackingEmbeddingProvider,
    index: InMemoryVectorIndex,
    *,
    pipeline_version: str = "test-index-v1",
) -> PolicyDocumentIndexer:
    return PolicyDocumentIndexer(
        embedding_provider=provider,
        vector_index=index,
        embedding_identity="test-embedding-v1",
        pipeline_version=pipeline_version,
    )


def _changed_chunk(chunk: PolicyChunk) -> PolicyChunk:
    content = f"{chunk.content}\n新增索引规则。"
    return chunk.model_copy(
        update={
            "content": content,
            "retrieval_text": f"{chunk.retrieval_text}\n新增索引规则。",
            "char_count": len(content),
            "content_hash": sha256(content.encode("utf-8")).hexdigest(),
        }
    )


def test_first_run_indexes_every_chunk_and_reports_added_documents(
    chunks_from_two_documents: list[PolicyChunk],
) -> None:
    provider = TrackingEmbeddingProvider()
    index = InMemoryVectorIndex(dimension=2)

    report = _indexer(provider, index).synchronize(chunks_from_two_documents)

    assert report.changed is True
    assert report.upserted_chunk_count == len(chunks_from_two_documents)
    assert report.deleted_chunk_count == 0
    assert index.size == len(chunks_from_two_documents)
    assert len(provider.document_batches) == 1
    assert len(provider.document_batches[0]) == len(chunks_from_two_documents)
    assert {result.status for result in report.documents} == {DocumentIndexingStatus.ADDED}
    assert all(
        len(entry.metadata[INDEX_FINGERPRINT_METADATA_KEY]) == 64 for entry in index.list_entries()
    )


def test_second_identical_run_skips_all_document_embeddings(
    chunks_from_two_documents: list[PolicyChunk],
) -> None:
    index = InMemoryVectorIndex(dimension=2)
    _indexer(TrackingEmbeddingProvider(), index).synchronize(chunks_from_two_documents)
    provider = TrackingEmbeddingProvider()

    report = _indexer(provider, index).synchronize(chunks_from_two_documents)

    assert report.changed is False
    assert report.upserted_chunk_count == 0
    assert report.unchanged_chunk_count == len(chunks_from_two_documents)
    assert provider.document_batches == []
    assert {result.status for result in report.documents} == {DocumentIndexingStatus.UNCHANGED}


def test_only_changed_chunk_is_reembedded(
    chunks_from_two_documents: list[PolicyChunk],
) -> None:
    index = InMemoryVectorIndex(dimension=2)
    _indexer(TrackingEmbeddingProvider(), index).synchronize(chunks_from_two_documents)
    updated = list(chunks_from_two_documents)
    updated[0] = _changed_chunk(updated[0])
    provider = TrackingEmbeddingProvider()

    report = _indexer(provider, index).synchronize(updated)

    assert report.upserted_chunk_count == 1
    assert provider.document_batches == [[updated[0].retrieval_text]]
    statuses = {result.document_id: result.status for result in report.documents}
    assert statuses[updated[0].document_id] is DocumentIndexingStatus.UPDATED


def test_authorization_metadata_change_invalidates_chunk_fingerprint(
    chunks_from_two_documents: list[PolicyChunk],
) -> None:
    index = InMemoryVectorIndex(dimension=2)
    _indexer(TrackingEmbeddingProvider(), index).synchronize(chunks_from_two_documents)
    updated = list(chunks_from_two_documents)
    updated[0] = updated[0].model_copy(update={"allowed_roles": ["FINANCE_MANAGER"]})
    provider = TrackingEmbeddingProvider()

    report = _indexer(provider, index).synchronize(updated)

    assert report.upserted_chunk_count == 1
    assert provider.document_batches == [[updated[0].retrieval_text]]
    entry = next(item for item in index.list_entries() if item.record_id == updated[0].chunk_id)
    assert entry.metadata["allowed_roles"] == '["FINANCE_MANAGER"]'


def test_stale_chunks_and_deleted_documents_are_removed(
    chunks_from_two_documents: list[PolicyChunk],
) -> None:
    index = InMemoryVectorIndex(dimension=2)
    _indexer(TrackingEmbeddingProvider(), index).synchronize(chunks_from_two_documents)
    deleted_document_id = chunks_from_two_documents[0].document_id
    remaining = [
        chunk for chunk in chunks_from_two_documents if chunk.document_id != deleted_document_id
    ]

    report = _indexer(TrackingEmbeddingProvider(), index).synchronize(remaining)

    expected_deleted = len(chunks_from_two_documents) - len(remaining)
    assert report.deleted_chunk_count == expected_deleted
    assert index.size == len(remaining)
    deleted = next(
        result for result in report.documents if result.document_id == deleted_document_id
    )
    assert deleted.status is DocumentIndexingStatus.DELETED
    assert deleted.deleted_chunk_count == expected_deleted


def test_pipeline_version_change_invalidates_every_chunk_fingerprint(
    chunks_from_two_documents: list[PolicyChunk],
) -> None:
    index = InMemoryVectorIndex(dimension=2)
    _indexer(
        TrackingEmbeddingProvider(),
        index,
        pipeline_version="test-index-v1",
    ).synchronize(chunks_from_two_documents)
    provider = TrackingEmbeddingProvider()

    report = _indexer(
        provider,
        index,
        pipeline_version="test-index-v2",
    ).synchronize(chunks_from_two_documents)

    assert report.upserted_chunk_count == len(chunks_from_two_documents)
    assert len(provider.document_batches[0]) == len(chunks_from_two_documents)


def test_embedding_count_failure_preserves_existing_snapshot(
    chunks_from_two_documents: list[PolicyChunk],
) -> None:
    index = InMemoryVectorIndex(dimension=2)
    _indexer(TrackingEmbeddingProvider(), index).synchronize(chunks_from_two_documents)
    before = index.list_entries()
    updated = list(chunks_from_two_documents)
    updated[0] = _changed_chunk(updated[0])

    with pytest.raises(RuntimeError, match="Embedding count"):
        _indexer(TrackingEmbeddingProvider(incomplete=True), index).synchronize(updated)

    assert index.list_entries() == before


def test_retriever_can_reuse_synchronized_vectors_without_embedding_documents(
    chunks_from_two_documents: list[PolicyChunk],
) -> None:
    index = InMemoryVectorIndex(dimension=2)
    _indexer(TrackingEmbeddingProvider(), index).synchronize(chunks_from_two_documents)
    provider = TrackingEmbeddingProvider()

    retriever = PolicyRetriever(
        embedding_provider=provider,
        chunks=chunks_from_two_documents,
        vector_index=index,
        index_vectors=False,
    )

    assert provider.document_batches == []
    assert retriever.size == len(chunks_from_two_documents)
    assert retriever.keyword_size == len(chunks_from_two_documents)
    assert retriever.search("差旅", top_k=1)


def test_retriever_rejects_incomplete_preindexed_store(
    chunks_from_two_documents: list[PolicyChunk],
) -> None:
    with pytest.raises(RuntimeError, match="missing synchronized chunks"):
        PolicyRetriever(
            embedding_provider=TrackingEmbeddingProvider(),
            chunks=chunks_from_two_documents,
            vector_index=InMemoryVectorIndex(dimension=2),
            index_vectors=False,
        )
