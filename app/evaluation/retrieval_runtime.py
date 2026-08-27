from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path

from app.evaluation.retrieval_models import RetrievalCase, RetrievalEvaluationMode
from app.portfolio.runtime import DeterministicLexicalEmbeddingProvider
from app.rag.bm25 import PolicyKeywordTokenizer
from app.rag.embeddings import BGEEmbeddingProvider, DEFAULT_BGE_MODEL_NAME
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_retriever import AccessControlledPolicyRetriever, PolicyRetriever
from app.rag.reranking import (
    BGERerankingProvider,
    DEFAULT_BGE_RERANKER_MODEL_NAME,
    RerankingProvider,
)
from app.schemas.chunk import PolicyChunk
from app.schemas.policy import SecurityLevel
from app.security import PolicyAccessContext, TrustedIdentitySource, authorized_chunk_ids

RETRIEVAL_EVALUATION_AS_OF_DATE = date(2026, 8, 20)


class RetrievalJudgmentError(ValueError):
    """A judged chunk is absent from, or unauthorized in, the evaluation corpus."""


class OfflineLexicalReranker(RerankingProvider):
    """Deterministic lexical reranker for CI wiring tests, not a BGE substitute."""

    def __init__(self) -> None:
        self._tokenizer = PolicyKeywordTokenizer()

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        query_terms = frozenset(self._tokenizer.tokenize(query))
        if not query_terms:
            raise ValueError("query must contain searchable terms")
        return [
            len(query_terms.intersection(self._tokenizer.tokenize(document))) / len(query_terms)
            for document in documents
        ]


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationRuntime:
    """Authorization-bound retriever and identities captured in the report."""

    retriever: AccessControlledPolicyRetriever
    chunks: tuple[PolicyChunk, ...]
    corpus_sha256: str
    embedding_provider: str
    reranker_provider: str
    embedding_batch_size: int
    reranker_batch_size: int
    requested_device: str | None
    external_model_calls: bool


def retrieval_evaluation_access_context() -> PolicyAccessContext:
    """Return the fixed trusted identity shared by retrieval experiments."""

    return PolicyAccessContext(
        employee_id="RETRIEVAL-EVAL-001",
        department="评测部门",
        roles=("EMPLOYEE",),
        security_clearance=SecurityLevel.INTERNAL,
        region="中国大陆",
        identity_source=TrustedIdentitySource.TEST_FIXTURE,
    )


def corpus_sha256(chunks: Sequence[PolicyChunk]) -> str:
    """Fingerprint stable chunk identities and content, independent of file ordering."""

    manifest = "\n".join(
        f"{chunk.chunk_id}:{chunk.content_hash}"
        for chunk in sorted(chunks, key=lambda x: x.chunk_id)
    )
    return sha256(manifest.encode("utf-8")).hexdigest()


def validate_retrieval_judgments(
    cases: Sequence[RetrievalCase],
    chunks: Sequence[PolicyChunk],
    *,
    access_context: PolicyAccessContext,
    as_of_date: date,
) -> None:
    """Fail before scoring if a label would be impossible to retrieve safely."""

    chunks_tuple = tuple(chunks)
    corpus_ids = frozenset(chunk.chunk_id for chunk in chunks_tuple)
    allowed_ids = authorized_chunk_ids(
        chunks_tuple,
        access_context,
        as_of_date=as_of_date,
    )
    for case in cases:
        missing = sorted(set(case.relevant_chunk_ids).difference(corpus_ids))
        if missing:
            raise RetrievalJudgmentError(
                f"{case.case_id} references missing chunks: {', '.join(missing)}"
            )
        unauthorized = sorted(set(case.relevant_chunk_ids).difference(allowed_ids))
        if unauthorized:
            raise RetrievalJudgmentError(
                f"{case.case_id} references unauthorized chunks: {', '.join(unauthorized)}"
            )


def build_retrieval_evaluation_runtime(
    *,
    policy_directory: str | Path,
    cases: Sequence[RetrievalCase],
    mode: RetrievalEvaluationMode,
    embedding_model: str = DEFAULT_BGE_MODEL_NAME,
    reranker_model: str = DEFAULT_BGE_RERANKER_MODEL_NAME,
    device: str | None = None,
    embedding_batch_size: int = 32,
    reranker_batch_size: int = 32,
    candidate_k: int = 20,
) -> RetrievalEvaluationRuntime:
    """Build the existing Retriever with either deterministic or real BGE providers."""

    if embedding_batch_size < 1 or reranker_batch_size < 1:
        raise ValueError("evaluation batch sizes must be greater than zero")

    chunks = tuple(chunk_policy_directory(Path(policy_directory)))
    access_context = retrieval_evaluation_access_context()
    validate_retrieval_judgments(
        cases,
        chunks,
        access_context=access_context,
        as_of_date=RETRIEVAL_EVALUATION_AS_OF_DATE,
    )

    if mode is RetrievalEvaluationMode.BGE:
        embedding_provider = BGEEmbeddingProvider(
            model_name=embedding_model,
            device=device,
            batch_size=embedding_batch_size,
        )
        reranking_provider: RerankingProvider = BGERerankingProvider(
            model_name=reranker_model,
            device=device,
            batch_size=reranker_batch_size,
        )
        embedding_identity = embedding_model
        reranker_identity = reranker_model
        external_model_calls = True
    else:
        embedding_provider = DeterministicLexicalEmbeddingProvider()
        reranking_provider = OfflineLexicalReranker()
        embedding_identity = "deterministic_hashed_lexical_v1"
        reranker_identity = "deterministic_lexical_overlap_v1"
        external_model_calls = False

    raw_retriever = PolicyRetriever(
        embedding_provider=embedding_provider,
        chunks=chunks,
        reranking_provider=reranking_provider,
        rerank_candidate_k=candidate_k,
    )
    return RetrievalEvaluationRuntime(
        retriever=raw_retriever.restrict(
            access_context,
            as_of_date=RETRIEVAL_EVALUATION_AS_OF_DATE,
        ),
        chunks=chunks,
        corpus_sha256=corpus_sha256(chunks),
        embedding_provider=embedding_identity,
        reranker_provider=reranker_identity,
        embedding_batch_size=embedding_batch_size,
        reranker_batch_size=reranker_batch_size,
        requested_device=device,
        external_model_calls=external_model_calls,
    )
