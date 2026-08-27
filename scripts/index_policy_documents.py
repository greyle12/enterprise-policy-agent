from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

from app.core.config import Settings, get_settings
from app.rag.collection_release import PgVectorCollectionReleaseManager
from app.rag.embeddings import (
    BGEEmbeddingProvider,
    DEFAULT_BGE_MODEL_NAME,
    EmbeddingProvider,
)
from app.rag.indexing import DocumentIndexingReport, PolicyDocumentIndexer
from app.rag.indexing_lease import (
    IndexingLeaseError,
    LeaseGuardedPgVectorIndex,
    PgVectorIndexingLeaseManager,
)
from app.rag.pgvector_index import PgVectorIndex
from app.rag.vector_index import VectorIndex, VectorStoreProviderName
from app.rag.vector_store import build_policy_vector_index

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_DEFAULT_LEASE_TTL_SECONDS = 900
_DEFAULT_LEASE_RENEW_INTERVAL_SECONDS = 60.0


def index_policy_documents(
    policy_directory: Path,
    *,
    settings: Settings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_index: VectorIndex | None = None,
) -> DocumentIndexingReport:
    """Run one index synchronization and close only resources created here."""

    active_settings = settings or get_settings()
    provider = embedding_provider or BGEEmbeddingProvider(
        model_name=DEFAULT_BGE_MODEL_NAME,
    )
    index = vector_index or build_policy_vector_index(
        active_settings,
        dimension=provider.dimension,
    )
    owns_index = vector_index is None
    try:
        return (
            PolicyDocumentIndexer(
                embedding_provider=provider,
                vector_index=index,
                embedding_identity=DEFAULT_BGE_MODEL_NAME,
                pipeline_version=active_settings.rag_index_pipeline_version,
            )
            .synchronize_directory(policy_directory)
            .report
        )
    finally:
        if owns_index:
            index.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Incrementally synchronize enterprise policy chunks into Vector Store."
    )
    parser.add_argument(
        "--policy-directory",
        type=Path,
        default=_DEFAULT_POLICY_DIRECTORY,
        help="Directory containing supported policy documents.",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help=(
            "Explicit physical pgvector collection for a blue/green build; "
            "disables release-alias resolution for this indexing run."
        ),
    )
    parser.add_argument(
        "--lease-owner",
        default=None,
        help="Auditable builder identity; defaults to hostname:process-id.",
    )
    parser.add_argument(
        "--lease-ttl-seconds",
        type=int,
        default=_DEFAULT_LEASE_TTL_SECONDS,
    )
    parser.add_argument(
        "--lease-renew-interval-seconds",
        type=float,
        default=_DEFAULT_LEASE_RENEW_INTERVAL_SECONDS,
    )
    return parser.parse_args(argv)


def _default_lease_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _index_pgvector_collection_with_lease(
    policy_directory: Path,
    *,
    settings: Settings,
    owner_id: str,
    ttl_seconds: int,
    renew_interval_seconds: float,
) -> tuple[DocumentIndexingReport, dict[str, object]]:
    provider = BGEEmbeddingProvider(model_name=DEFAULT_BGE_MODEL_NAME)
    index = build_policy_vector_index(settings, dimension=provider.dimension)
    if not isinstance(index, PgVectorIndex):
        index.close()
        raise RuntimeError("distributed indexing leases require a PgVectorIndex")
    manager = None
    release_manager = None
    try:
        manager = PgVectorIndexingLeaseManager.from_dsn(
            settings.rag_pgvector_dsn.get_secret_value(),
            min_pool_size=1,
            max_pool_size=min(2, settings.rag_pgvector_max_pool_size),
            connect_timeout_seconds=settings.rag_pgvector_connect_timeout_seconds,
        )
        release_manager = PgVectorCollectionReleaseManager.from_dsn(
            settings.rag_pgvector_dsn.get_secret_value(),
            min_pool_size=1,
            max_pool_size=min(2, settings.rag_pgvector_max_pool_size),
            connect_timeout_seconds=settings.rag_pgvector_connect_timeout_seconds,
        )
        release_manager.initialize_schema()
        manager.initialize_schema()
        with manager.maintained(
            collection_name=index.collection_name,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
            renew_interval_seconds=renew_interval_seconds,
        ) as lease_session:
            guarded_index = LeaseGuardedPgVectorIndex(index, lease_session)
            report = index_policy_documents(
                policy_directory,
                settings=settings,
                embedding_provider=provider,
                vector_index=guarded_index,
            )
            lease_session.require_healthy()
            lease = lease_session.lease
        return report, {
            "collection_name": lease.collection_name,
            "owner_id": lease.owner_id,
            "fencing_token": lease.fencing_token,
            "released": True,
        }
    finally:
        if release_manager is not None:
            release_manager.close()
        if manager is not None:
            manager.close()
        index.close()


def _run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    if args.collection is not None:
        settings = settings.model_copy(
            update={
                "rag_pgvector_collection": args.collection,
                "rag_pgvector_release_alias": None,
            }
        )
    lease_payload = None
    if settings.rag_vector_store_provider is VectorStoreProviderName.PGVECTOR:
        if args.collection is None:
            raise ValueError("pgvector indexing requires an explicit --collection Green target")
        report, lease_payload = _index_pgvector_collection_with_lease(
            args.policy_directory,
            settings=settings,
            owner_id=args.lease_owner or _default_lease_owner(),
            ttl_seconds=args.lease_ttl_seconds,
            renew_interval_seconds=args.lease_renew_interval_seconds,
        )
    else:
        report = index_policy_documents(
            args.policy_directory,
            settings=settings,
        )
    payload = {
        "schema_version": "1.0",
        "phase": 36 if lease_payload is not None else 30,
        "passed": True,
        "vector_store_provider": settings.rag_vector_store_provider.value,
        "lease": lease_payload,
        **report.to_dict(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except (IndexingLeaseError, RuntimeError, ValueError) as exc:
        print(f"Policy document indexing failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
