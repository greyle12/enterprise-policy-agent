from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.rag.bm25 import (
    DEFAULT_BM25_B,
    DEFAULT_BM25_K1,
    BM25Record,
    InMemoryBM25Index,
)
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_retriever import PolicyRetriever, RetrievalMethod
from app.schemas.policy import SecurityLevel
from app.security import PolicyAccessContext, TrustedIdentitySource

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"


class _OfflineEmbeddingProvider:
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
        employee_id="BM25-VERIFY-001",
        department="技术部",
        roles=("EMPLOYEE",),
        security_clearance=SecurityLevel.INTERNAL,
        region="中国大陆",
        identity_source=TrustedIdentitySource.TEST_FIXTURE,
    )


def _authorization_scope_score_isolated() -> bool:
    authorized = BM25Record(record_id="authorized", text="差旅 住宿 发票")
    unauthorized = BM25Record(
        record_id="unauthorized",
        text="差旅 差旅 差旅 核心机密",
    )
    scoped_index = InMemoryBM25Index()
    scoped_index.add([authorized])
    full_index = InMemoryBM25Index()
    full_index.add([authorized, unauthorized])

    expected = scoped_index.search("差旅", allowed_record_ids={"authorized"})
    actual = full_index.search("差旅", allowed_record_ids={"authorized"})
    return (
        len(expected) == 1
        and len(actual) == 1
        and actual[0].record.record_id == "authorized"
        and abs(actual[0].score - expected[0].score) < 1e-12
        and not full_index.search("核心机密", allowed_record_ids={"authorized"})
    )


def run_verification() -> dict[str, object]:
    """Verify Phase 26 lexical retrieval and its authorization boundary offline."""

    chunks = chunk_policy_directory(_POLICY_DIRECTORY)
    embedding_provider = _OfflineEmbeddingProvider()
    retriever = PolicyRetriever(
        embedding_provider=embedding_provider,
        chunks=chunks,
    )

    travel_results = retriever.search_keywords("住宿发票", top_k=3)
    identifier_results = retriever.search_keywords(
        "INFORMATION_SECURITY_POLICY_001",
        top_k=5,
    )
    keyword_query_uses_embedding = bool(embedding_provider.query_inputs)

    base_chunk = chunks[0]
    unauthorized = base_chunk.model_copy(
        update={
            "chunk_id": "verify-unauthorized-core",
            "retrieval_text": "核心机密项目代号 BLACK-ORCHID",
            "security_level": SecurityLevel.CORE,
        }
    )
    authorized = base_chunk.model_copy(
        update={
            "chunk_id": "verify-authorized-internal",
            "retrieval_text": "普通内部差旅报销规定",
            "security_level": SecurityLevel.INTERNAL,
        }
    )
    secured_retriever = PolicyRetriever(
        embedding_provider=_OfflineEmbeddingProvider(),
        chunks=[unauthorized, authorized],
    ).restrict(_access_context(), as_of_date=date(2026, 8, 20))
    unauthorized_results = secured_retriever.search_keywords("BLACK-ORCHID")

    vector_results = retriever.search("原有向量查询", top_k=1)
    checks = {
        "existing_policy_corpus_is_indexed": retriever.keyword_size == len(chunks) == 199,
        "exact_policy_terms_rank_relevant_document": (
            bool(travel_results)
            and travel_results[0].chunk.document_id == "TRAVEL_POLICY_001"
            and travel_results[0].retrieval_method is RetrievalMethod.BM25
        ),
        "enterprise_identifier_is_searchable": (
            bool(identifier_results)
            and all(
                result.chunk.document_id == "INFORMATION_SECURITY_POLICY_001"
                for result in identifier_results
            )
        ),
        "keyword_query_does_not_call_embedding": not keyword_query_uses_embedding,
        "authorization_precedes_candidate_selection": (
            secured_retriever.allowed_chunk_count == 1 and not unauthorized_results
        ),
        "authorization_scope_owns_bm25_statistics": (_authorization_scope_score_isolated()),
        "existing_vector_channel_remains_available": (
            len(vector_results) == 1
            and vector_results[0].retrieval_method is RetrievalMethod.VECTOR
            and embedding_provider.query_inputs == ["原有向量查询"]
        ),
    }

    return {
        "schema_version": "1.0",
        "phase": 26,
        "passed": all(checks.values()),
        "document_count": len({chunk.document_id for chunk in chunks}),
        "chunk_count": len(chunks),
        "keyword_index_size": retriever.keyword_size,
        "bm25_k1": DEFAULT_BM25_K1,
        "bm25_b": DEFAULT_BM25_B,
        "network_calls": False,
        "model_calls": False,
        "hybrid_search_enabled": False,
        "checks": checks,
    }


def main() -> int:
    try:
        report = run_verification()
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        report = {
            "schema_version": "1.0",
            "phase": 26,
            "passed": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
