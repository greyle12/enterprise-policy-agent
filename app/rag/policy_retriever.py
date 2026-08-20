from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol, Self

from app.rag.document_loader import (
    DEFAULT_DOCUMENT_LOADER_REGISTRY,
    DocumentLoaderRegistry,
)
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.vector_index import (
    InMemoryVectorIndex,
    VectorRecord,
)
from app.schemas.chunk import PolicyChunk
from app.security import PolicyAccessContext, authorized_chunk_ids


class EmbeddingProvider(Protocol):
    """PolicyRetriever 依赖的最小 Embedding 接口。"""

    @property
    def dimension(self) -> int:
        """返回向量维度。"""

    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """为制度文本生成向量。"""

    def embed_query(
        self,
        text: str,
    ) -> list[float]:
        """为用户问题生成向量。"""


@dataclass(frozen=True, slots=True)
class PolicyRetrievalResult:
    """一次制度检索命中结果。"""

    chunk: PolicyChunk
    score: float


def _build_record_metadata(
    chunk: PolicyChunk,
) -> dict[str, str]:
    """将引用所需字段保存到向量记录元数据。"""

    metadata = {
        "document_id": chunk.document_id,
        "document_title": chunk.document_title,
        "document_version": chunk.document_version,
        "document_status": chunk.document_status.value,
        "issuing_department": chunk.issuing_department,
        "chapter_title": chunk.chapter_title,
        "article_label": chunk.article_label,
        "article_title": chunk.article_title,
        "source_path": str(chunk.source_path),
        "source_media_type": chunk.source_media_type,
        "source_line_start": str(chunk.source_line_start),
        "source_line_end": str(chunk.source_line_end),
        "security_level": chunk.security_level.value,
        "content_hash": chunk.content_hash,
    }
    if chunk.metadata_source_path is not None:
        metadata["metadata_source_path"] = str(chunk.metadata_source_path)
    if chunk.source_page_start is not None and chunk.source_page_end is not None:
        metadata["source_page_start"] = str(chunk.source_page_start)
        metadata["source_page_end"] = str(chunk.source_page_end)
    if chunk.source_block_start is not None and chunk.source_block_end is not None:
        metadata["source_block_start"] = str(chunk.source_block_start)
        metadata["source_block_end"] = str(chunk.source_block_end)
    return metadata


class PolicyRetriever:
    """负责制度向量索引构建和语义检索。"""

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        chunks: Sequence[PolicyChunk],
    ) -> None:
        chunk_list = list(chunks)

        if not chunk_list:
            raise ValueError("chunks must not be empty")

        chunks_by_id = {chunk.chunk_id: chunk for chunk in chunk_list}

        if len(chunks_by_id) != len(chunk_list):
            raise ValueError("chunk_id values must be unique")

        retrieval_texts = [chunk.retrieval_text for chunk in chunk_list]
        vectors = embedding_provider.embed_documents(retrieval_texts)

        if len(vectors) != len(chunk_list):
            raise RuntimeError(
                f"Embedding count does not match chunk count: {len(vectors)} != {len(chunk_list)}"
            )

        records = [
            VectorRecord(
                record_id=chunk.chunk_id,
                text=chunk.content,
                vector=vector,
                metadata=_build_record_metadata(chunk),
            )
            for chunk, vector in zip(
                chunk_list,
                vectors,
                strict=True,
            )
        ]

        index = InMemoryVectorIndex(dimension=embedding_provider.dimension)
        index.add(records)

        self._embedding_provider = embedding_provider
        self._chunks = tuple(chunk_list)
        self._chunks_by_id = chunks_by_id
        self._index = index

    @classmethod
    def from_directory(
        cls,
        policy_directory: Path,
        *,
        embedding_provider: EmbeddingProvider,
        loader_registry: DocumentLoaderRegistry = DEFAULT_DOCUMENT_LOADER_REGISTRY,
    ) -> Self:
        """解析指定目录并建立制度检索器。"""

        chunks = chunk_policy_directory(
            policy_directory,
            loader_registry=loader_registry,
        )

        return cls(
            embedding_provider=embedding_provider,
            chunks=chunks,
        )

    @property
    def size(self) -> int:
        """返回当前索引中的 Chunk 数量。"""

        return self._index.size

    @property
    def dimension(self) -> int:
        """返回索引向量维度。"""

        return self._embedding_provider.dimension

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        allowed_chunk_ids: Collection[str] | None = None,
    ) -> list[PolicyRetrievalResult]:
        """只在调用方预先授权的 Chunk 范围内执行向量评分。"""

        normalized_query = query.strip()

        if not normalized_query:
            raise ValueError("query must not be blank")

        if top_k < 1:
            raise ValueError("top_k must be greater than zero")

        query_vector = self._embedding_provider.embed_query(normalized_query)
        vector_results = self._index.search(
            query_vector,
            top_k=top_k,
            allowed_record_ids=allowed_chunk_ids,
        )

        return [
            PolicyRetrievalResult(
                chunk=self._chunks_by_id[result.record.record_id],
                score=result.score,
            )
            for result in vector_results
        ]

    def restrict(
        self,
        access_context: PolicyAccessContext,
        *,
        as_of_date: date | None = None,
    ) -> AccessControlledPolicyRetriever:
        """Bind a trusted identity and compute its searchable IDs before retrieval."""

        return AccessControlledPolicyRetriever(
            retriever=self,
            chunks=self._chunks,
            access_context=access_context,
            as_of_date=as_of_date,
        )


class AccessControlledPolicyRetriever:
    """A fixed-identity search view that cannot broaden its own policy scope."""

    def __init__(
        self,
        *,
        retriever: PolicyRetriever,
        chunks: tuple[PolicyChunk, ...],
        access_context: PolicyAccessContext,
        as_of_date: date | None,
    ) -> None:
        self._retriever = retriever
        self._chunks = chunks
        self._access_context = access_context
        self._as_of_date = as_of_date

    def _allowed_chunk_ids(self) -> frozenset[str]:
        return authorized_chunk_ids(
            self._chunks,
            self._access_context,
            as_of_date=self._as_of_date or date.today(),
        )

    @property
    def allowed_chunk_count(self) -> int:
        return len(self._allowed_chunk_ids())

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[PolicyRetrievalResult]:
        return self._retriever.search(
            query,
            top_k=top_k,
            allowed_chunk_ids=self._allowed_chunk_ids(),
        )
