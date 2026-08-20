from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from datetime import date
from hashlib import sha256
from pathlib import Path

from app.rag.indexing import DocumentIndexingStatus, PolicyDocumentIndexer
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_retriever import PolicyRetriever
from app.rag.vector_index import InMemoryVectorIndex, SearchResult
from app.schemas.policy import SecurityLevel
from app.security import PolicyAccessContext, TrustedIdentitySource

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_AS_OF_DATE = date(2026, 8, 20)


class _TrackingEmbeddingProvider:
    def __init__(self) -> None:
        self.document_batches: list[list[str]] = []

    @property
    def dimension(self) -> int:
        return 2

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        batch = list(texts)
        self.document_batches.append(batch)
        vectors: list[list[float]] = []
        for text in batch:
            if "PHASE30-CORE" in text:
                vectors.append([1.0, 0.0])
            elif "PHASE30-OBSOLETE" in text:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.8, 0.2])
        return vectors

    def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("text must not be blank")
        return [1.0, 0.0]


class _TrackingIndex(InMemoryVectorIndex):
    def __init__(self) -> None:
        super().__init__(dimension=2)
        self.search_allowed_ids: frozenset[str] | None = None

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        *,
        allowed_record_ids: Collection[str] | None = None,
    ) -> list[SearchResult]:
        self.search_allowed_ids = (
            None if allowed_record_ids is None else frozenset(allowed_record_ids)
        )
        return super().search(
            query_vector,
            top_k,
            allowed_record_ids=allowed_record_ids,
        )


def _content_update(chunk, *, marker: str, security_level: SecurityLevel):
    content = marker
    return chunk.model_copy(
        update={
            "chunk_id": marker.lower(),
            "document_id": marker.lower(),
            "document_title": marker,
            "document_version": "1.0",
            "content": content,
            "retrieval_text": content,
            "security_level": security_level,
            "char_count": len(content),
            "content_hash": sha256(content.encode("utf-8")).hexdigest(),
        }
    )


def _access_context() -> PolicyAccessContext:
    return PolicyAccessContext(
        employee_id="PHASE30-VERIFY-001",
        department="演示部门",
        roles=("EMPLOYEE",),
        security_clearance=SecurityLevel.INTERNAL,
        region="中国大陆",
        identity_source=TrustedIdentitySource.TEST_FIXTURE,
    )


def run_verification() -> dict[str, object]:
    base = chunk_policy_directory(_POLICY_DIRECTORY)[0]
    authorized = _content_update(
        base,
        marker="PHASE30-INTERNAL",
        security_level=SecurityLevel.INTERNAL,
    )
    unauthorized = _content_update(
        base,
        marker="PHASE30-CORE",
        security_level=SecurityLevel.CORE,
    )
    obsolete = _content_update(
        base,
        marker="PHASE30-OBSOLETE",
        security_level=SecurityLevel.INTERNAL,
    )

    provider = _TrackingEmbeddingProvider()
    index = _TrackingIndex()
    indexer = PolicyDocumentIndexer(
        embedding_provider=provider,
        vector_index=index,
        embedding_identity="phase30-offline-embedding-v1",
        pipeline_version="phase30-index-v1",
    )

    first = indexer.synchronize([authorized, unauthorized, obsolete])
    second = indexer.synchronize([authorized, unauthorized, obsolete])
    updated_authorized = _content_update(
        authorized,
        marker="PHASE30-INTERNAL-UPDATED",
        security_level=SecurityLevel.INTERNAL,
    ).model_copy(update={"chunk_id": authorized.chunk_id, "document_id": authorized.document_id})
    third = indexer.synchronize([updated_authorized, unauthorized])

    raw_retriever = PolicyRetriever(
        embedding_provider=provider,
        chunks=[updated_authorized, unauthorized],
        vector_index=index,
        index_vectors=False,
    )
    secured = raw_retriever.restrict(_access_context(), as_of_date=_AS_OF_DATE)
    results = secured.search("制度", top_k=2)
    env_example = (_PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    compose = (_PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")

    checks = {
        "first_run_indexes_all_chunks": (
            first.upserted_chunk_count == 3
            and {result.status for result in first.documents} == {DocumentIndexingStatus.ADDED}
        ),
        "second_run_is_idempotent": (
            not second.changed
            and second.upserted_chunk_count == 0
            and second.unchanged_chunk_count == 3
        ),
        "only_changed_chunk_is_reembedded": (
            third.upserted_chunk_count == 1
            and provider.document_batches[-1] == [updated_authorized.retrieval_text]
        ),
        "stale_chunk_is_deleted": (
            third.deleted_chunk_count == 1
            and obsolete.chunk_id not in {entry.record_id for entry in index.list_entries()}
        ),
        "retriever_reuses_preindexed_vectors": len(provider.document_batches) == 2,
        "authorization_still_precedes_vector_scoring": (
            index.search_allowed_ids == frozenset({authorized.chunk_id})
            and [result.chunk.chunk_id for result in results] == [authorized.chunk_id]
        ),
        "pipeline_version_is_explicitly_configured": (
            "RAG_INDEX_PIPELINE_VERSION=policy-index-v1" in env_example
            and "RAG_INDEX_PIPELINE_VERSION:" in compose
        ),
    }
    return {
        "schema_version": "1.0",
        "phase": 30,
        "passed": all(checks.values()),
        "runtime_provider": "offline_incremental_index_contract",
        "database_calls": False,
        "network_calls": False,
        "initial_chunk_count": first.total_chunk_count,
        "no_op_embedding_count": second.upserted_chunk_count,
        "incremental_upsert_count": third.upserted_chunk_count,
        "stale_delete_count": third.deleted_chunk_count,
        "checks": checks,
    }


def main() -> int:
    try:
        report = run_verification()
    except Exception as exc:
        report = {
            "schema_version": "1.0",
            "phase": 30,
            "passed": False,
            "error": type(exc).__name__,
            "message": str(exc),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
