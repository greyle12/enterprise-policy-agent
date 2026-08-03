from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Self

from app.rag.policy_chunker import chunk_policy_directory
from app.rag.vector_index import (
    InMemoryVectorIndex,
    VectorRecord,
)
from app.schemas.chunk import PolicyChunk


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

    return {
        "document_id": chunk.document_id,
        "document_title": chunk.document_title,
        "document_version": chunk.document_version,
        "document_status": chunk.document_status.value,
        "issuing_department": chunk.issuing_department,
        "chapter_title": chunk.chapter_title,
        "article_label": chunk.article_label,
        "article_title": chunk.article_title,
        "source_path": str(chunk.source_path),
        "source_line_start": str(
            chunk.source_line_start
        ),
        "source_line_end": str(
            chunk.source_line_end
        ),
        "security_level": chunk.security_level.value,
        "content_hash": chunk.content_hash,
    }


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

        chunks_by_id = {
            chunk.chunk_id: chunk
            for chunk in chunk_list
        }

        if len(chunks_by_id) != len(chunk_list):
            raise ValueError(
                "chunk_id values must be unique"
            )

        retrieval_texts = [
            chunk.retrieval_text
            for chunk in chunk_list
        ]
        vectors = embedding_provider.embed_documents(
            retrieval_texts
        )

        if len(vectors) != len(chunk_list):
            raise RuntimeError(
                "Embedding count does not match chunk count: "
                f"{len(vectors)} != {len(chunk_list)}"
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

        index = InMemoryVectorIndex(
            dimension=embedding_provider.dimension
        )
        index.add(records)

        self._embedding_provider = embedding_provider
        self._chunks_by_id = chunks_by_id
        self._index = index

    @classmethod
    def from_directory(
        cls,
        policy_directory: Path,
        *,
        embedding_provider: EmbeddingProvider,
    ) -> Self:
        """解析指定目录并建立制度检索器。"""

        chunks = chunk_policy_directory(
            policy_directory
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
    ) -> list[PolicyRetrievalResult]:
        """根据用户问题返回最相关的制度 Chunk。"""

        normalized_query = query.strip()

        if not normalized_query:
            raise ValueError("query must not be blank")

        if top_k < 1:
            raise ValueError(
                "top_k must be greater than zero"
            )

        query_vector = (
            self._embedding_provider.embed_query(
                normalized_query
            )
        )
        vector_results = self._index.search(
            query_vector,
            top_k=top_k,
        )

        return [
            PolicyRetrievalResult(
                chunk=self._chunks_by_id[
                    result.record.record_id
                ],
                score=result.score,
            )
            for result in vector_results
        ]