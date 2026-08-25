from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from contextlib import AbstractContextManager
from math import ceil, isfinite, sqrt
from typing import Any, Protocol, Self

from app.rag.embeddings import EmbeddingVector
from app.rag.vector_index import SearchResult, VectorIndexEntry, VectorRecord

PGVECTOR_TABLE_NAME = "rag_policy_vectors"


class _CursorLike(Protocol):
    def fetchone(self) -> Sequence[Any] | None:
        """Return one database row."""

    def fetchall(self) -> Sequence[Sequence[Any]]:
        """Return all database rows."""


class _BatchCursorLike(Protocol):
    def executemany(
        self,
        query: str,
        params_seq: Sequence[Sequence[Any]],
    ) -> object:
        """Execute one parameterized statement for many rows."""


class _ConnectionLike(Protocol):
    def execute(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> _CursorLike:
        """Execute one SQL statement."""

    def cursor(self) -> AbstractContextManager[_BatchCursorLike]:
        """Open a cursor for Psycopg batch operations."""


class PgVectorConnectionPool(Protocol):
    """Small pool boundary that keeps the adapter testable without PostgreSQL."""

    def connection(self) -> AbstractContextManager[_ConnectionLike]:
        """Borrow one transactional connection."""

    def close(self) -> None:
        """Close the pool."""


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(format(float(value), ".17g") for value in vector) + "]"


def _parse_vector(value: object) -> list[float]:
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized.startswith("[") or not normalized.endswith("]"):
            raise RuntimeError("pgvector returned an invalid vector value")
        body = normalized[1:-1].strip()
        return [] if not body else [float(item) for item in body.split(",")]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [float(item) for item in value]
    raise RuntimeError("pgvector returned an unsupported vector value")


def _parse_metadata(value: object) -> dict[str, str]:
    if isinstance(value, Mapping):
        return {str(key): str(item) for key, item in value.items()}
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, Mapping):
            return {str(key): str(item) for key, item in decoded.items()}
    raise RuntimeError("pgvector returned invalid JSON metadata")


class PgVectorIndex:
    """Synchronous PostgreSQL/pgvector index with authorization-scoped search."""

    def __init__(
        self,
        *,
        pool: PgVectorConnectionPool,
        dimension: int,
        collection_name: str,
        owns_pool: bool = False,
    ) -> None:
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
            raise ValueError("dimension must be greater than zero")
        normalized_collection = collection_name.strip()
        if not normalized_collection:
            raise ValueError("collection_name must not be blank")

        self._pool = pool
        self._dimension = dimension
        self._collection_name = normalized_collection
        self._owns_pool = owns_pool
        self._closed = False

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        dimension: int,
        collection_name: str,
        min_pool_size: int = 1,
        max_pool_size: int = 4,
        connect_timeout_seconds: float = 5.0,
    ) -> Self:
        """Open a Psycopg connection pool without importing it for memory-only runs."""

        normalized_dsn = dsn.strip()
        if not normalized_dsn:
            raise ValueError("dsn must not be blank")
        if min_pool_size < 1 or max_pool_size < min_pool_size:
            raise ValueError("pool sizes must satisfy 1 <= min_pool_size <= max_pool_size")
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be greater than zero")
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - exercised only in broken installs
            raise RuntimeError(
                "pgvector storage requires the psycopg pool dependency; reinstall the project"
            ) from exc

        pool = ConnectionPool(
            conninfo=normalized_dsn,
            min_size=min_pool_size,
            max_size=max_pool_size,
            kwargs={"connect_timeout": max(1, ceil(connect_timeout_seconds))},
            timeout=connect_timeout_seconds,
            open=True,
        )
        try:
            pool.wait(timeout=connect_timeout_seconds)
        except BaseException:
            pool.close()
            raise
        return cls(
            pool=pool,
            dimension=dimension,
            collection_name=collection_name,
            owns_pool=True,
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def collection_name(self) -> str:
        return self._collection_name

    @property
    def size(self) -> int:
        self._ensure_open()
        with self._pool.connection() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM {PGVECTOR_TABLE_NAME} WHERE collection_name = %s",
                (self._collection_name,),
            ).fetchone()
        if row is None:
            raise RuntimeError("pgvector count query returned no row")
        return int(row[0])

    def initialize_schema(self) -> None:
        """Create the extension and exact-search table idempotently."""

        self._ensure_open()
        statements = (
            "CREATE EXTENSION IF NOT EXISTS vector",
            f"""
            CREATE TABLE IF NOT EXISTS {PGVECTOR_TABLE_NAME} (
                collection_name TEXT NOT NULL,
                record_id TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding VECTOR NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (collection_name, record_id)
            )
            """,
            f"""
            DO $pgvector_dimension_migration$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_attribute AS attribute
                    JOIN pg_class AS relation
                      ON relation.oid = attribute.attrelid
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = current_schema()
                      AND relation.relname = '{PGVECTOR_TABLE_NAME}'
                      AND attribute.attname = 'embedding'
                      AND NOT attribute.attisdropped
                      AND format_type(attribute.atttypid, attribute.atttypmod) <> 'vector'
                ) THEN
                    ALTER TABLE {PGVECTOR_TABLE_NAME}
                    ALTER COLUMN embedding TYPE VECTOR
                    USING embedding::vector;
                END IF;
            END
            $pgvector_dimension_migration$
            """,
            f"""
            CREATE INDEX IF NOT EXISTS idx_rag_policy_vectors_collection
            ON {PGVECTOR_TABLE_NAME} (collection_name)
            """,
        )
        with self._pool.connection() as connection:
            for statement in statements:
                connection.execute(statement)

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        """Persist a validated batch using one transactional executemany call."""

        self.apply_changes(records)

    def list_entries(self) -> list[VectorIndexEntry]:
        """Load synchronization metadata without transferring vector payloads."""

        self._ensure_open()
        with self._pool.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT record_id, metadata
                FROM {PGVECTOR_TABLE_NAME}
                WHERE collection_name = %s
                ORDER BY record_id
                """,
                (self._collection_name,),
            ).fetchall()
        return [
            VectorIndexEntry(
                record_id=str(row[0]),
                metadata=_parse_metadata(row[1]),
            )
            for row in rows
        ]

    def apply_changes(
        self,
        records: Sequence[VectorRecord],
        *,
        delete_record_ids: Collection[str] = (),
    ) -> None:
        """Apply upserts and stale-record deletion in one database transaction."""

        self._ensure_open()
        prepared = self._prepare_records(records)
        delete_ids = self._prepare_delete_ids(delete_record_ids)
        upsert_ids = {str(row[1]) for row in prepared}
        overlap = sorted(upsert_ids.intersection(delete_ids))
        if overlap:
            raise ValueError(
                "record ids cannot be upserted and deleted together: " + ", ".join(overlap)
            )
        if not prepared and not delete_ids:
            return

        query = f"""
            INSERT INTO {PGVECTOR_TABLE_NAME} (
                collection_name,
                record_id,
                text,
                embedding,
                metadata
            ) VALUES (%s, %s, %s, %s::vector, %s::jsonb)
            ON CONFLICT (collection_name, record_id) DO UPDATE SET
                text = EXCLUDED.text,
                embedding = EXCLUDED.embedding,
                metadata = EXCLUDED.metadata,
                updated_at = CURRENT_TIMESTAMP
        """
        with self._pool.connection() as connection:
            if prepared:
                with connection.cursor() as cursor:
                    cursor.executemany(query, prepared)
            if delete_ids:
                connection.execute(
                    f"""
                    DELETE FROM {PGVECTOR_TABLE_NAME}
                    WHERE collection_name = %s
                      AND record_id = ANY(%s)
                    """,
                    (self._collection_name, list(delete_ids)),
                )

    def search(
        self,
        query_vector: EmbeddingVector,
        top_k: int = 5,
        *,
        allowed_record_ids: Collection[str] | None = None,
    ) -> list[SearchResult]:
        """Apply the authorization allow-list in SQL before cosine ordering."""

        self._ensure_open()
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be greater than zero")
        self._validate_vector(query_vector, label="query vector")

        allowed = None if allowed_record_ids is None else tuple(sorted(set(allowed_record_ids)))
        if allowed == ():
            return []

        authorization_sql = ""
        params: list[object] = [self._collection_name]
        if allowed is not None:
            authorization_sql = " AND record_id = ANY(%s)"
            params.append(list(allowed))
        params.extend((_vector_literal(query_vector), top_k))
        query = f"""
            WITH authorized_records AS MATERIALIZED (
                SELECT record_id, text, embedding, metadata
                FROM {PGVECTOR_TABLE_NAME}
                WHERE collection_name = %s{authorization_sql}
            ),
            query_vector AS (
                SELECT %s::vector AS query_embedding
            )
            SELECT
                authorized_records.record_id,
                authorized_records.text,
                authorized_records.embedding::text,
                authorized_records.metadata,
                1 - (
                    authorized_records.embedding <=> query_vector.query_embedding
                ) AS score
            FROM authorized_records
            CROSS JOIN query_vector
            ORDER BY authorized_records.embedding <=> query_vector.query_embedding
            LIMIT %s
        """
        with self._pool.connection() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()

        results: list[SearchResult] = []
        for row in rows:
            vector = _parse_vector(row[2])
            self._validate_vector(vector, label="stored vector")
            score = float(row[4])
            if not isfinite(score):
                raise RuntimeError("pgvector returned a non-finite similarity score")
            results.append(
                SearchResult(
                    record=VectorRecord(
                        record_id=str(row[0]),
                        text=str(row[1]),
                        vector=vector,
                        metadata=_parse_metadata(row[3]),
                    ),
                    score=score,
                )
            )
        return results

    def ping(self) -> None:
        """Verify both PostgreSQL reachability and the pgvector schema."""

        self._ensure_open()
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT
                    EXISTS (
                        SELECT 1 FROM pg_extension WHERE extname = 'vector'
                    ),
                    to_regclass('rag_policy_vectors') IS NOT NULL
                """
            ).fetchone()
        if row is None or not bool(row[0]) or not bool(row[1]):
            raise RuntimeError("PostgreSQL is missing the pgvector application schema")

    def delete_collection(self) -> None:
        """Delete only this explicitly named collection, primarily for verification cleanup."""

        self._ensure_open()
        with self._pool.connection() as connection:
            connection.execute(
                f"DELETE FROM {PGVECTOR_TABLE_NAME} WHERE collection_name = %s",
                (self._collection_name,),
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_pool:
            self._pool.close()

    def _prepare_records(self, records: Sequence[VectorRecord]) -> list[tuple[object, ...]]:
        prepared: list[tuple[object, ...]] = []
        identifiers: set[str] = set()
        for record in records:
            normalized_id = record.record_id.strip()
            if not normalized_id:
                raise ValueError("record_id must not be empty")
            if normalized_id in identifiers:
                raise ValueError(f"record id already exists in batch: {normalized_id}")
            self._validate_vector(record.vector, label="vector")
            prepared.append(
                (
                    self._collection_name,
                    normalized_id,
                    record.text,
                    _vector_literal(record.vector),
                    json.dumps(record.metadata, ensure_ascii=False, sort_keys=True),
                )
            )
            identifiers.add(normalized_id)
        return prepared

    def _validate_vector(self, vector: Sequence[float], *, label: str) -> None:
        if len(vector) != self._dimension:
            raise ValueError(
                f"vector dimension mismatch: expected {self._dimension}, got {len(vector)}"
            )
        if any(not isfinite(value) for value in vector):
            raise ValueError(f"{label} must contain only finite values")
        if sqrt(sum(value * value for value in vector)) == 0.0:
            raise ValueError(f"{label} must not be a zero vector")

    @staticmethod
    def _prepare_delete_ids(record_ids: Collection[str]) -> tuple[str, ...]:
        normalized: set[str] = set()
        for record_id in record_ids:
            value = record_id.strip()
            if not value:
                raise ValueError("delete record ids must not be blank")
            normalized.add(value)
        return tuple(sorted(normalized))

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("pgvector index is closed")
