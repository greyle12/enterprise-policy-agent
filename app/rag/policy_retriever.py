from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Protocol, Self

from app.rag.bm25 import (
    BM25Record,
    BM25UnsearchableQueryError,
    InMemoryBM25Index,
    KeywordTokenizer,
)
from app.rag.document_loader import (
    DEFAULT_DOCUMENT_LOADER_REGISTRY,
    DocumentLoaderRegistry,
)
from app.rag.fusion import (
    DEFAULT_RRF_RANK_CONSTANT,
    RankedList,
    reciprocal_rank_fusion,
)
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.reranking import (
    RerankCandidate,
    RerankingProvider,
    rerank_candidates,
)
from app.rag.vector_index import (
    InMemoryVectorIndex,
    VectorRecord,
)
from app.schemas.chunk import PolicyChunk
from app.security import PolicyAccessContext, authorized_chunk_ids

DEFAULT_HYBRID_CANDIDATE_K = 20
DEFAULT_RERANK_CANDIDATE_K = 20


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


class RetrievalMethod(StrEnum):
    """The retrieval channel that produced a ranked result."""

    VECTOR = "vector"
    BM25 = "bm25"
    HYBRID = "hybrid"
    RERANKED = "reranked"


@dataclass(frozen=True, slots=True)
class RetrievalSignal:
    """One source ranking that contributed to a hybrid result."""

    method: RetrievalMethod
    rank: int
    raw_score: float
    rrf_contribution: float


@dataclass(frozen=True, slots=True)
class PolicyRetrievalResult:
    """一次制度检索命中结果。"""

    chunk: PolicyChunk
    score: float
    retrieval_method: RetrievalMethod = RetrievalMethod.VECTOR
    retrieval_signals: tuple[RetrievalSignal, ...] = ()
    pre_rerank_score: float | None = None
    pre_rerank_rank: int | None = None


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
    if chunk.source_ocr_applied:
        metadata["source_ocr_engine"] = chunk.source_ocr_engine or ""
        metadata["source_ocr_unit_kind"] = chunk.source_ocr_unit_kind or ""
        metadata["source_ocr_unit_numbers"] = ",".join(
            str(number) for number in chunk.source_ocr_unit_numbers
        )
        metadata["source_ocr_confidence_min"] = str(chunk.source_ocr_confidence_min)
    return metadata


class PolicyRetriever:
    """Build vector/BM25 indexes and expose independent or fused retrieval."""

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        chunks: Sequence[PolicyChunk],
        keyword_tokenizer: KeywordTokenizer | None = None,
        reranking_provider: RerankingProvider | None = None,
        rerank_candidate_k: int = DEFAULT_RERANK_CANDIDATE_K,
    ) -> None:
        chunk_list = list(chunks)

        if not chunk_list:
            raise ValueError("chunks must not be empty")

        chunks_by_id = {chunk.chunk_id: chunk for chunk in chunk_list}

        if len(chunks_by_id) != len(chunk_list):
            raise ValueError("chunk_id values must be unique")
        if (
            isinstance(rerank_candidate_k, bool)
            or not isinstance(rerank_candidate_k, int)
            or rerank_candidate_k < 1
        ):
            raise ValueError("rerank_candidate_k must be greater than zero")

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

        keyword_index = InMemoryBM25Index(tokenizer=keyword_tokenizer)
        keyword_index.add(
            [
                BM25Record(
                    record_id=chunk.chunk_id,
                    text=chunk.retrieval_text,
                    metadata=_build_record_metadata(chunk),
                )
                for chunk in chunk_list
            ]
        )

        self._embedding_provider = embedding_provider
        self._chunks = tuple(chunk_list)
        self._chunks_by_id = chunks_by_id
        self._index = index
        self._keyword_index = keyword_index
        self._reranking_provider = reranking_provider
        self._rerank_candidate_k = rerank_candidate_k

    @classmethod
    def from_directory(
        cls,
        policy_directory: Path,
        *,
        embedding_provider: EmbeddingProvider,
        loader_registry: DocumentLoaderRegistry = DEFAULT_DOCUMENT_LOADER_REGISTRY,
        keyword_tokenizer: KeywordTokenizer | None = None,
        reranking_provider: RerankingProvider | None = None,
        rerank_candidate_k: int = DEFAULT_RERANK_CANDIDATE_K,
    ) -> Self:
        """解析指定目录并建立制度检索器。"""

        chunks = chunk_policy_directory(
            policy_directory,
            loader_registry=loader_registry,
        )

        return cls(
            embedding_provider=embedding_provider,
            chunks=chunks,
            keyword_tokenizer=keyword_tokenizer,
            reranking_provider=reranking_provider,
            rerank_candidate_k=rerank_candidate_k,
        )

    @property
    def size(self) -> int:
        """返回当前索引中的 Chunk 数量。"""

        return self._index.size

    @property
    def dimension(self) -> int:
        """返回索引向量维度。"""

        return self._embedding_provider.dimension

    @property
    def keyword_size(self) -> int:
        """Return the number of chunks in the BM25 index."""

        return self._keyword_index.size

    @property
    def reranker_enabled(self) -> bool:
        """Return whether a second-stage relevance provider is configured."""

        return self._reranking_provider is not None

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

        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
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

    def search_keywords(
        self,
        query: str,
        *,
        top_k: int = 5,
        allowed_chunk_ids: Collection[str] | None = None,
    ) -> list[PolicyRetrievalResult]:
        """Run BM25 only inside the caller-provided authorized chunk scope."""

        keyword_results = self._keyword_index.search(
            query,
            top_k=top_k,
            allowed_record_ids=allowed_chunk_ids,
        )
        return [
            PolicyRetrievalResult(
                chunk=self._chunks_by_id[result.record.record_id],
                score=result.score,
                retrieval_method=RetrievalMethod.BM25,
            )
            for result in keyword_results
        ]

    def search_hybrid(
        self,
        query: str,
        *,
        top_k: int = 5,
        candidate_k: int | None = None,
        rank_constant: int = DEFAULT_RRF_RANK_CONSTANT,
        allowed_chunk_ids: Collection[str] | None = None,
    ) -> list[PolicyRetrievalResult]:
        """Fuse authorization-scoped Vector and BM25 rankings with RRF."""

        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be blank")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be greater than zero")

        resolved_candidate_k = (
            max(DEFAULT_HYBRID_CANDIDATE_K, top_k) if candidate_k is None else candidate_k
        )
        if (
            isinstance(resolved_candidate_k, bool)
            or not isinstance(resolved_candidate_k, int)
            or resolved_candidate_k < top_k
        ):
            raise ValueError("candidate_k must be greater than or equal to top_k")

        vector_results = self.search(
            normalized_query,
            top_k=resolved_candidate_k,
            allowed_chunk_ids=allowed_chunk_ids,
        )
        try:
            keyword_results = self.search_keywords(
                normalized_query,
                top_k=resolved_candidate_k,
                allowed_chunk_ids=allowed_chunk_ids,
            )
        except BM25UnsearchableQueryError:
            keyword_results = []
        results_by_method = {
            RetrievalMethod.VECTOR: {result.chunk.chunk_id: result for result in vector_results},
            RetrievalMethod.BM25: {result.chunk.chunk_id: result for result in keyword_results},
        }
        fused_results = reciprocal_rank_fusion(
            [
                RankedList(
                    source=RetrievalMethod.VECTOR.value,
                    record_ids=tuple(result.chunk.chunk_id for result in vector_results),
                ),
                RankedList(
                    source=RetrievalMethod.BM25.value,
                    record_ids=tuple(result.chunk.chunk_id for result in keyword_results),
                ),
            ],
            rank_constant=rank_constant,
            top_k=top_k,
        )

        hybrid_results: list[PolicyRetrievalResult] = []
        for fused_result in fused_results:
            signals = tuple(
                RetrievalSignal(
                    method=RetrievalMethod(contribution.source),
                    rank=contribution.rank,
                    raw_score=results_by_method[RetrievalMethod(contribution.source)][
                        fused_result.record_id
                    ].score,
                    rrf_contribution=contribution.score,
                )
                for contribution in fused_result.contributions
            )
            hybrid_results.append(
                PolicyRetrievalResult(
                    chunk=self._chunks_by_id[fused_result.record_id],
                    score=fused_result.score,
                    retrieval_method=RetrievalMethod.HYBRID,
                    retrieval_signals=signals,
                )
            )
        return hybrid_results

    def search_reranked(
        self,
        query: str,
        *,
        top_k: int = 5,
        candidate_k: int | None = None,
        rank_constant: int = DEFAULT_RRF_RANK_CONSTANT,
        allowed_chunk_ids: Collection[str] | None = None,
    ) -> list[PolicyRetrievalResult]:
        """Rerank an authorization-scoped RRF candidate pool in one provider call."""

        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be greater than zero")
        resolved_candidate_k = (
            max(self._rerank_candidate_k, top_k) if candidate_k is None else candidate_k
        )
        if (
            isinstance(resolved_candidate_k, bool)
            or not isinstance(resolved_candidate_k, int)
            or resolved_candidate_k < top_k
        ):
            raise ValueError("candidate_k must be greater than or equal to top_k")
        if self._reranking_provider is None:
            return self.search_hybrid(
                query,
                top_k=top_k,
                candidate_k=resolved_candidate_k,
                rank_constant=rank_constant,
                allowed_chunk_ids=allowed_chunk_ids,
            )

        hybrid_results = self.search_hybrid(
            query,
            top_k=resolved_candidate_k,
            candidate_k=resolved_candidate_k,
            rank_constant=rank_constant,
            allowed_chunk_ids=allowed_chunk_ids,
        )
        reranked = rerank_candidates(
            query,
            [
                RerankCandidate(
                    candidate_id=result.chunk.chunk_id,
                    text=result.chunk.retrieval_text,
                    retrieval_score=result.score,
                )
                for result in hybrid_results
            ],
            provider=self._reranking_provider,
            top_k=top_k,
        )
        hybrid_by_id = {result.chunk.chunk_id: result for result in hybrid_results}
        return [
            PolicyRetrievalResult(
                chunk=hybrid_by_id[result.candidate.candidate_id].chunk,
                score=result.rerank_score,
                retrieval_method=RetrievalMethod.RERANKED,
                retrieval_signals=hybrid_by_id[result.candidate.candidate_id].retrieval_signals,
                pre_rerank_score=result.candidate.retrieval_score,
                pre_rerank_rank=result.original_rank + 1,
            )
            for result in reranked
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

    def search_keywords(
        self,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[PolicyRetrievalResult]:
        return self._retriever.search_keywords(
            query,
            top_k=top_k,
            allowed_chunk_ids=self._allowed_chunk_ids(),
        )

    def search_hybrid(
        self,
        query: str,
        *,
        top_k: int = 5,
        candidate_k: int | None = None,
        rank_constant: int = DEFAULT_RRF_RANK_CONSTANT,
    ) -> list[PolicyRetrievalResult]:
        allowed_chunk_ids = self._allowed_chunk_ids()
        return self._retriever.search_hybrid(
            query,
            top_k=top_k,
            candidate_k=candidate_k,
            rank_constant=rank_constant,
            allowed_chunk_ids=allowed_chunk_ids,
        )

    def search_reranked(
        self,
        query: str,
        *,
        top_k: int = 5,
        candidate_k: int | None = None,
        rank_constant: int = DEFAULT_RRF_RANK_CONSTANT,
    ) -> list[PolicyRetrievalResult]:
        allowed_chunk_ids = self._allowed_chunk_ids()
        return self._retriever.search_reranked(
            query,
            top_k=top_k,
            candidate_k=candidate_k,
            rank_constant=rank_constant,
            allowed_chunk_ids=allowed_chunk_ids,
        )
