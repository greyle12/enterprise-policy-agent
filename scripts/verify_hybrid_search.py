from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

from app.portfolio.runtime import DeterministicLexicalEmbeddingProvider
from app.rag.fusion import DEFAULT_RRF_RANK_CONSTANT, RankedList, reciprocal_rank_fusion
from app.rag.policy_answer_service import PolicyAnswerService
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_retriever import (
    DEFAULT_HYBRID_CANDIDATE_K,
    PolicyRetriever,
    RetrievalMethod,
)
from app.schemas.policy import SecurityLevel
from app.security import PolicyAccessContext, TrustedIdentitySource

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_AS_OF_DATE = date(2026, 8, 20)


class _TrackingLexicalEmbeddingProvider(DeterministicLexicalEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.query_inputs: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.query_inputs.append(text)
        return super().embed_query(text)


class _OfflineCitationLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages) -> str:
        if not messages:
            raise ValueError("messages must not be empty")
        self.calls += 1
        return "离线 Hybrid Search 验收已获得制度依据。[S1]"


def _access_context() -> PolicyAccessContext:
    return PolicyAccessContext(
        employee_id="HYBRID-VERIFY-001",
        department="演示部门",
        roles=("EMPLOYEE",),
        security_clearance=SecurityLevel.INTERNAL,
        region="中国大陆",
        identity_source=TrustedIdentitySource.TEST_FIXTURE,
    )


def run_verification() -> dict[str, object]:
    """Verify Phase 27 Vector/BM25 RRF fusion and security offline."""

    chunks = chunk_policy_directory(_POLICY_DIRECTORY)
    embedding_provider = _TrackingLexicalEmbeddingProvider()
    raw_retriever = PolicyRetriever(
        embedding_provider=embedding_provider,
        chunks=chunks,
    )
    retriever = raw_retriever.restrict(_access_context(), as_of_date=_AS_OF_DATE)

    travel_results = retriever.search_hybrid("出差住宿费如何报销", top_k=5)
    identifier_results = retriever.search_hybrid(
        "INFORMATION_SECURITY_POLICY_001",
        top_k=5,
    )

    base_chunk = chunks[0]
    unauthorized = base_chunk.model_copy(
        update={
            "chunk_id": "hybrid-verify-unauthorized",
            "retrieval_text": "核心机密项目代号 BLACK-ORCHID",
            "security_level": SecurityLevel.CORE,
        }
    )
    authorized = base_chunk.model_copy(
        update={
            "chunk_id": "hybrid-verify-authorized",
            "retrieval_text": "普通内部差旅制度",
            "security_level": SecurityLevel.INTERNAL,
        }
    )
    secured = PolicyRetriever(
        embedding_provider=_TrackingLexicalEmbeddingProvider(),
        chunks=[unauthorized, authorized],
    ).restrict(_access_context(), as_of_date=_AS_OF_DATE)
    secured_results = secured.search_hybrid("BLACK-ORCHID", top_k=2)

    sample_fusion = reciprocal_rank_fusion(
        [
            RankedList(source="vector", record_ids=("shared", "vector-only")),
            RankedList(source="bm25", record_ids=("shared", "bm25-only")),
        ]
    )

    llm = _OfflineCitationLLM()
    answer = asyncio.run(
        PolicyAnswerService(retriever=retriever, llm_client=llm).answer("出差住宿费如何报销")
    )

    first_travel = travel_results[0] if travel_results else None
    checks = {
        "existing_vector_and_bm25_indexes_are_reused": (
            raw_retriever.size == raw_retriever.keyword_size == len(chunks) == 199
        ),
        "travel_query_combines_semantic_and_lexical_signals": (
            first_travel is not None
            and first_travel.chunk.document_id == "TRAVEL_POLICY_001"
            and first_travel.retrieval_method is RetrievalMethod.HYBRID
            and {signal.method for signal in first_travel.retrieval_signals}
            == {RetrievalMethod.VECTOR, RetrievalMethod.BM25}
            and abs(
                first_travel.score
                - sum(signal.rrf_contribution for signal in first_travel.retrieval_signals)
            )
            < 1e-12
        ),
        "exact_identifier_survives_hybrid_fusion": (
            bool(identifier_results)
            and all(
                result.chunk.document_id == "INFORMATION_SECURITY_POLICY_001"
                for result in identifier_results
            )
        ),
        "rrf_deduplicates_multi_channel_candidate": (
            sample_fusion[0].record_id == "shared"
            and len(sample_fusion[0].contributions) == 2
            and len({result.record_id for result in sample_fusion}) == len(sample_fusion)
        ),
        "authorization_filters_both_channels_before_fusion": (
            secured.allowed_chunk_count == 1
            and all(
                result.chunk.chunk_id != "hybrid-verify-unauthorized" for result in secured_results
            )
        ),
        "policy_answer_service_uses_hybrid_results": (
            bool(answer.citations)
            and answer.citations[0].document_title == "差旅报销管理制度"
            and llm.calls == 1
        ),
    }

    return {
        "schema_version": "1.0",
        "phase": 27,
        "passed": all(checks.values()),
        "document_count": len({chunk.document_id for chunk in chunks}),
        "chunk_count": len(chunks),
        "vector_index_size": raw_retriever.size,
        "bm25_index_size": raw_retriever.keyword_size,
        "rrf_rank_constant": DEFAULT_RRF_RANK_CONSTANT,
        "default_candidate_k": DEFAULT_HYBRID_CANDIDATE_K,
        "embedding_fixture": "deterministic_lexical_hash_v1",
        "network_calls": False,
        "external_model_calls": False,
        "offline_llm_fixture_calls": llm.calls,
        "verification_scope": "hybrid_rrf_without_reranker",
        "checks": checks,
    }


def main() -> int:
    try:
        report = run_verification()
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        report = {
            "schema_version": "1.0",
            "phase": 27,
            "passed": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
