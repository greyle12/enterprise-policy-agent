from typing import Protocol

from pydantic import SecretStr

from app.rag.pgvector_index import PgVectorIndex
from app.rag.vector_index import (
    InMemoryVectorIndex,
    VectorIndex,
    VectorStoreProviderName,
)


class VectorStoreSettings(Protocol):
    rag_vector_store_provider: VectorStoreProviderName
    rag_pgvector_dsn: SecretStr
    rag_pgvector_collection: str
    rag_pgvector_min_pool_size: int
    rag_pgvector_max_pool_size: int
    rag_pgvector_connect_timeout_seconds: float


def build_policy_vector_index(
    settings: VectorStoreSettings,
    *,
    dimension: int,
) -> VectorIndex:
    """Create and initialize the configured vector storage backend."""

    if settings.rag_vector_store_provider is VectorStoreProviderName.MEMORY:
        return InMemoryVectorIndex(dimension=dimension)

    index = PgVectorIndex.from_dsn(
        settings.rag_pgvector_dsn.get_secret_value(),
        dimension=dimension,
        collection_name=settings.rag_pgvector_collection,
        min_pool_size=settings.rag_pgvector_min_pool_size,
        max_pool_size=settings.rag_pgvector_max_pool_size,
        connect_timeout_seconds=settings.rag_pgvector_connect_timeout_seconds,
    )
    try:
        index.initialize_schema()
    except BaseException:
        index.close()
        raise
    return index


__all__ = ["VectorStoreSettings", "build_policy_vector_index"]
