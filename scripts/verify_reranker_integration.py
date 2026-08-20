from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from app.portfolio.runtime import DeterministicLexicalEmbeddingProvider
from app.rag.bm25 import PolicyKeywordTokenizer
from app.rag.policy_answer_service import PolicyAnswerService
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_retriever import (
    DEFAULT_RERANK_CANDIDATE_K,
    PolicyRetriever,
    RetrievalMethod,
)
from app.rag.reranking import (
    DEFAULT_BGE_RERANKER_MODEL_NAME,
    RerankingProvider,
)
from app.schemas.policy import SecurityLevel
from app.security import PolicyAccessContext, TrustedIdentitySource

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_AS_OF_DATE = date(2026, 8, 20)


class _OfflineLexicalReranker(RerankingProvider):
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self._tokenizer = PolicyKeywordTokenizer()

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        document_list = list(documents)
        self.calls.append((query, document_list))
        query_terms = set(self._tokenizer.tokenize(query))
        return [
            len(query_terms & set(self._tokenizer.tokenize(document))) / max(1, len(query_terms))
            for document in document_list
        ]


class _OfflineCitationLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages) -> str:
        if not messages:
            raise ValueError("messages must not be empty")
        self.calls += 1
        return "离线 Reranker 验收获得了有效制度证据。[S1]"


def _access_context() -> PolicyAccessContext:
    return PolicyAccessContext(
        employee_id="RERANK-VERIFY-001",
        department="演示部门",
        roles=("EMPLOYEE",),
        security_clearance=SecurityLevel.INTERNAL,
        region="中国大陆",
        identity_source=TrustedIdentitySource.TEST_FIXTURE,
    )


def run_verification() -> dict[str, object]:
    """Verify the authorized RRF-to-reranker production boundary offline."""

    chunks = chunk_policy_directory(_POLICY_DIRECTORY)
    reranker = _OfflineLexicalReranker()
    raw_retriever = PolicyRetriever(
        embedding_provider=DeterministicLexicalEmbeddingProvider(),
        chunks=chunks,
        reranking_provider=reranker,
        rerank_candidate_k=DEFAULT_RERANK_CANDIDATE_K,
    )
    retriever = raw_retriever.restrict(_access_context(), as_of_date=_AS_OF_DATE)

    reranked_results = retriever.search_reranked("住宿发票", top_k=5)
    identifier_results = retriever.search_reranked(
        "INFORMATION_SECURITY_POLICY_001",
        top_k=5,
    )

    base_chunk = chunks[0]
    secret = "CORE-RERANKER-SECRET"
    unauthorized = base_chunk.model_copy(
        update={
            "chunk_id": "reranker-verify-unauthorized",
            "retrieval_text": f"核心机密制度 {secret}",
            "security_level": SecurityLevel.CORE,
        }
    )
    authorized = base_chunk.model_copy(
        update={
            "chunk_id": "reranker-verify-authorized",
            "retrieval_text": "普通内部差旅制度",
            "security_level": SecurityLevel.INTERNAL,
        }
    )
    secured_reranker = _OfflineLexicalReranker()
    secured = PolicyRetriever(
        embedding_provider=DeterministicLexicalEmbeddingProvider(),
        chunks=[unauthorized, authorized],
        reranking_provider=secured_reranker,
    ).restrict(_access_context(), as_of_date=_AS_OF_DATE)
    secured_results = secured.search_reranked("CORE-RERANKER-SECRET", top_k=1)

    disabled = PolicyRetriever(
        embedding_provider=DeterministicLexicalEmbeddingProvider(),
        chunks=chunks,
    )
    fallback_results = disabled.search_reranked("住宿发票", top_k=1)

    llm = _OfflineCitationLLM()
    answer = asyncio.run(
        PolicyAnswerService(retriever=retriever, llm_client=llm).answer("住宿发票")
    )

    first_result = reranked_results[0] if reranked_results else None
    checks = {
        "existing_hybrid_indexes_are_reused": (
            raw_retriever.size == raw_retriever.keyword_size == len(chunks) == 199
        ),
        "rrf_candidate_pool_is_scored_in_one_batch": (
            len(reranker.calls) >= 1 and len(reranker.calls[0][1]) == DEFAULT_RERANK_CANDIDATE_K
        ),
        "reranker_changes_order_and_preserves_fusion_diagnostics": (
            first_result is not None
            and first_result.chunk.document_id == "TRAVEL_POLICY_001"
            and first_result.chunk.article_label == "第十六条"
            and first_result.retrieval_method is RetrievalMethod.RERANKED
            and first_result.pre_rerank_rank == 2
            and first_result.pre_rerank_score is not None
            and bool(first_result.retrieval_signals)
        ),
        "exact_identifier_remains_relevant_after_reranking": (
            bool(identifier_results)
            and all(
                result.chunk.document_id == "INFORMATION_SECURITY_POLICY_001"
                for result in identifier_results
            )
        ),
        "authorization_precedes_reranker_input": (
            secured.allowed_chunk_count == 1
            and bool(secured_results)
            and all(secret not in document for document in secured_reranker.calls[0][1])
        ),
        "disabled_provider_falls_back_to_rrf": (
            not disabled.reranker_enabled
            and bool(fallback_results)
            and fallback_results[0].retrieval_method is RetrievalMethod.HYBRID
        ),
        "policy_answer_service_uses_reranked_results": (
            bool(answer.citations)
            and answer.citations[0].document_title == "差旅报销管理制度"
            and llm.calls == 1
        ),
    }

    return {
        "schema_version": "1.0",
        "phase": 28,
        "passed": all(checks.values()),
        "document_count": len({chunk.document_id for chunk in chunks}),
        "chunk_count": len(chunks),
        "vector_index_size": raw_retriever.size,
        "bm25_index_size": raw_retriever.keyword_size,
        "rerank_candidate_k": DEFAULT_RERANK_CANDIDATE_K,
        "configured_bge_model": DEFAULT_BGE_RERANKER_MODEL_NAME,
        "runtime_provider": "offline_lexical_fixture",
        "network_calls": False,
        "external_model_calls": False,
        "real_bge_model_loaded": False,
        "offline_llm_fixture_calls": llm.calls,
        "checks": checks,
    }


def main() -> int:
    try:
        report = run_verification()
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        report = {
            "schema_version": "1.0",
            "phase": 28,
            "passed": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
