from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from math import isfinite, sqrt
from typing import Self
from uuid import uuid4

from app.rag.embeddings import EmbeddingVector
from app.rag.pgvector_index import (
    PGVECTOR_TABLE_NAME,
    PgVectorConnectionPool,
    _parse_metadata,
    _parse_vector,
    _vector_literal,
)
from app.rag.vector_index import SearchResult, VectorIndexEntry, VectorRecord

_EXPERIMENT_ID = re.compile(r"^[a-f0-9]{12,32}$")


class PgVectorHnswExperimentIndex:
    """Read-only HNSW index built only from one pre-authorized evaluation scope."""

    def __init__(
        self,
        *,
        pool: PgVectorConnectionPool,
        dimension: int,
        source_collection: str,
        experiment_id: str | None = None,
        owns_pool: bool = False,
    ) -> None:
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
            raise ValueError("dimension must be greater than zero")
        normalized_collection = source_collection.strip()
        if not normalized_collection:
            raise ValueError("source_collection must not be blank")
        resolved_id = experiment_id or uuid4().hex[:16]
        if _EXPERIMENT_ID.fullmatch(resolved_id) is None:
            raise ValueError("experiment_id must contain 12-32 lowercase hexadecimal characters")

        self._pool = pool
        self._dimension = dimension
        self._source_collection = normalized_collection
        self._table_name = f"rag_policy_hnsw_exp_{resolved_id}"
        self._index_name = f"idx_{self._table_name}_embedding"
        self._owns_pool = owns_pool
        self._closed = False
        self._prepared = False
        self._authorized_ids: frozenset[str] = frozenset()
        self._ef_search = 40

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        dimension: int,
        source_collection: str,
        experiment_id: str | None = None,
        connect_timeout_seconds: float = 5.0,
    ) -> Self:
        normalized_dsn = dsn.strip()
        if not normalized_dsn:
            raise ValueError("dsn must not be blank")
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be greater than zero")
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - broken installation only
            raise RuntimeError(
                "pgvector HNSW experiments require the psycopg pool dependency"
            ) from exc

        pool = ConnectionPool(
            conninfo=normalized_dsn,
            min_size=1,
            max_size=2,
            kwargs={"connect_timeout": max(1, round(connect_timeout_seconds))},
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
            source_collection=source_collection,
            experiment_id=experiment_id,
            owns_pool=True,
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def size(self) -> int:
        self._ensure_prepared()
        return len(self._authorized_ids)

    @property
    def table_name(self) -> str:
        return self._table_name

    def prepare(
        self,
        *,
        authorized_record_ids: Collection[str],
        m: int,
        ef_construction: int,
        ef_search: int,
    ) -> None:
        """Materialize authorization first, then build an isolated HNSW graph."""

        self._ensure_open()
        if self._prepared:
            raise RuntimeError("HNSW experiment index is already prepared")
        authorized_ids = _normalize_authorized_ids(authorized_record_ids)
        _validate_hnsw_parameters(m=m, ef_construction=ef_construction, ef_search=ef_search)

        statements: tuple[tuple[str, Sequence[object] | None], ...] = (
            (
                f"""
                CREATE UNLOGGED TABLE {self._table_name} (
                    record_id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    embedding VECTOR({self._dimension}) NOT NULL,
                    metadata JSONB NOT NULL
                )
                """,
                None,
            ),
            (
                f"""
                INSERT INTO {self._table_name} (record_id, text, embedding, metadata)
                SELECT record_id, text, embedding, metadata
                FROM {PGVECTOR_TABLE_NAME}
                WHERE collection_name = %s
                  AND record_id = ANY(%s)
                """,
                (self._source_collection, list(sorted(authorized_ids))),
            ),
            (
                f"""
                CREATE INDEX {self._index_name}
                ON {self._table_name}
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = {m}, ef_construction = {ef_construction})
                """,
                None,
            ),
            (f"ANALYZE {self._table_name}", None),
        )
        table_created = False
        try:
            with self._pool.connection() as connection:
                for position, (query, params) in enumerate(statements):
                    connection.execute(query, params)
                    if position == 0:
                        table_created = True
                row = connection.execute(f"SELECT COUNT(*) FROM {self._table_name}").fetchone()
            actual_count = int(row[0]) if row is not None else -1
            if actual_count != len(authorized_ids):
                raise RuntimeError(
                    "authorized HNSW scope is incomplete: "
                    f"expected {len(authorized_ids)}, copied {actual_count}"
                )
        except BaseException:
            if table_created:
                self._drop_table()
            raise

        self._authorized_ids = authorized_ids
        self._ef_search = ef_search
        self._prepared = True

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        raise RuntimeError("HNSW experiment indexes are read-only")

    def apply_changes(
        self,
        records: Sequence[VectorRecord],
        *,
        delete_record_ids: Collection[str] = (),
    ) -> None:
        raise RuntimeError("HNSW experiment indexes are read-only")

    def list_entries(self) -> list[VectorIndexEntry]:
        self._ensure_prepared()
        return [VectorIndexEntry(record_id=value) for value in sorted(self._authorized_ids)]

    def search(
        self,
        query_vector: EmbeddingVector,
        top_k: int = 5,
        *,
        allowed_record_ids: Collection[str] | None = None,
    ) -> list[SearchResult]:
        """Search a graph that contains only the materialized authorized records."""

        self._ensure_prepared()
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be greater than zero")
        _validate_vector(query_vector, dimension=self._dimension)
        requested_scope = (
            self._authorized_ids
            if allowed_record_ids is None
            else _normalize_authorized_ids(allowed_record_ids)
        )
        if requested_scope != self._authorized_ids:
            raise ValueError(
                "search authorization scope must match the pre-materialized experiment scope"
            )

        query = f"""
            SELECT
                record_id,
                text,
                embedding::text,
                metadata,
                1 - (embedding <=> %s::vector) AS score
            FROM {self._table_name}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        vector_literal = _vector_literal(query_vector)
        with self._pool.connection() as connection:
            connection.execute(
                "SELECT set_config('hnsw.ef_search', %s, true)",
                (str(self._ef_search),),
            )
            connection.execute("SET LOCAL enable_seqscan = off")
            rows = connection.execute(
                query,
                (vector_literal, vector_literal, top_k),
            ).fetchall()
        return [_result_from_row(row, dimension=self._dimension) for row in rows]

    def ping(self) -> None:
        self._ensure_prepared()
        with self._pool.connection() as connection:
            row = connection.execute(f"SELECT to_regclass('{self._table_name}')").fetchone()
        if row is None or row[0] is None:
            raise RuntimeError("HNSW experiment table is unavailable")

    def cleanup(self) -> None:
        if self._closed:
            return
        self._drop_table()
        self._prepared = False
        self._authorized_ids = frozenset()

    def close(self) -> None:
        if self._closed:
            return
        self.cleanup()
        self._closed = True
        if self._owns_pool:
            self._pool.close()

    def _drop_table(self) -> None:
        with self._pool.connection() as connection:
            connection.execute(f"DROP TABLE IF EXISTS {self._table_name}")

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("HNSW experiment index is closed")

    def _ensure_prepared(self) -> None:
        self._ensure_open()
        if not self._prepared:
            raise RuntimeError("HNSW experiment index is not prepared")


def _normalize_authorized_ids(record_ids: Collection[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for record_id in record_ids:
        value = record_id.strip()
        if not value:
            raise ValueError("authorized record ids must not be blank")
        normalized.add(value)
    if not normalized:
        raise ValueError("authorized record ids must not be empty")
    return frozenset(normalized)


def _validate_hnsw_parameters(*, m: int, ef_construction: int, ef_search: int) -> None:
    values = (m, ef_construction, ef_search)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise ValueError("HNSW parameters must be integers")
    if not 2 <= m <= 100:
        raise ValueError("HNSW m must be between 2 and 100")
    if not 4 <= ef_construction <= 1_000:
        raise ValueError("HNSW ef_construction must be between 4 and 1000")
    if ef_construction < 2 * m:
        raise ValueError("HNSW ef_construction must be at least twice m")
    if not 1 <= ef_search <= 1_000:
        raise ValueError("HNSW ef_search must be between 1 and 1000")


def _validate_vector(vector: Sequence[float], *, dimension: int) -> None:
    if len(vector) != dimension:
        raise ValueError(f"vector dimension mismatch: expected {dimension}, got {len(vector)}")
    if any(not isfinite(value) for value in vector):
        raise ValueError("query vector must contain only finite values")
    if sqrt(sum(value * value for value in vector)) == 0.0:
        raise ValueError("query vector must not be a zero vector")


def _result_from_row(row: Sequence[object], *, dimension: int) -> SearchResult:
    vector = _parse_vector(row[2])
    _validate_vector(vector, dimension=dimension)
    score = float(row[4])
    if not isfinite(score):
        raise RuntimeError("pgvector returned a non-finite similarity score")
    return SearchResult(
        record=VectorRecord(
            record_id=str(row[0]),
            text=str(row[1]),
            vector=vector,
            metadata=_parse_metadata(row[3]),
        ),
        score=score,
    )
