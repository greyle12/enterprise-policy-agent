from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import ceil
from typing import Self

from app.rag.indexing import (
    EMBEDDING_IDENTITY_METADATA_KEY,
    INDEX_FINGERPRINT_METADATA_KEY,
    INDEX_PIPELINE_VERSION_METADATA_KEY,
    index_snapshot_sha256_from_entries,
)
from app.rag.indexing_lease import (
    assert_collection_has_no_active_lease,
    initialize_indexing_lease_schema,
)
from app.rag.pgvector_index import (
    PGVECTOR_TABLE_NAME,
    PgVectorConnectionPool,
    _ConnectionLike,
)

RELEASE_POINTER_TABLE = "rag_vector_collection_releases"
RELEASE_HISTORY_TABLE = "rag_vector_collection_release_history"
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")


class CollectionReleaseAction(StrEnum):
    PUBLISH = "publish"
    ROLLBACK = "rollback"


class CollectionReleaseError(RuntimeError):
    """Base error for vector collection release operations."""


class CollectionReleaseConflictError(CollectionReleaseError):
    """Raised when a stale generation attempts to mutate the release pointer."""


class CollectionReleaseValidationError(CollectionReleaseError):
    """Raised when a target collection is empty, incomplete, or incompatible."""


@dataclass(frozen=True, slots=True)
class CollectionReleasePointer:
    alias: str
    active_collection: str
    previous_collection: str | None
    generation: int
    embedding_identity: str
    pipeline_version: str
    record_count: int
    snapshot_sha256: str
    updated_at: datetime | str


def initialize_collection_release_schema(connection: _ConnectionLike) -> None:
    """Create release pointers, history, and their shared lease boundary."""

    statements = (
        f"""
        CREATE TABLE IF NOT EXISTS {RELEASE_POINTER_TABLE} (
            alias TEXT PRIMARY KEY,
            active_collection TEXT NOT NULL,
            previous_collection TEXT,
            generation BIGINT NOT NULL CHECK (generation >= 1),
            embedding_identity TEXT NOT NULL,
            pipeline_version TEXT NOT NULL,
            record_count INTEGER NOT NULL CHECK (record_count > 0),
            snapshot_sha256 TEXT NOT NULL CHECK (snapshot_sha256 ~ '^[0-9a-f]{{64}}$'),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {RELEASE_HISTORY_TABLE} (
            alias TEXT NOT NULL,
            generation BIGINT NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('publish', 'rollback')),
            collection_name TEXT NOT NULL,
            replaced_collection TEXT,
            embedding_identity TEXT NOT NULL,
            pipeline_version TEXT NOT NULL,
            record_count INTEGER NOT NULL CHECK (record_count > 0),
            snapshot_sha256 TEXT NOT NULL CHECK (snapshot_sha256 ~ '^[0-9a-f]{{64}}$'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (alias, generation)
        )
        """,
    )
    for statement in statements:
        connection.execute(statement)
    initialize_indexing_lease_schema(connection)


class PgVectorCollectionReleaseManager:
    """Transactional active collection pointer with compare-and-swap rollback."""

    def __init__(
        self,
        *,
        pool: PgVectorConnectionPool,
        owns_pool: bool = False,
    ) -> None:
        self._pool = pool
        self._owns_pool = owns_pool
        self._closed = False

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 2,
        connect_timeout_seconds: float = 5.0,
    ) -> Self:
        normalized_dsn = dsn.strip()
        if not normalized_dsn:
            raise ValueError("dsn must not be blank")
        if min_pool_size < 1 or max_pool_size < min_pool_size:
            raise ValueError("pool sizes must satisfy 1 <= min_pool_size <= max_pool_size")
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be greater than zero")
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - broken installation only
            raise RuntimeError("collection releases require the psycopg pool dependency") from exc

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
        return cls(pool=pool, owns_pool=True)

    def initialize_schema(self) -> None:
        self._ensure_open()
        with self._pool.connection() as connection:
            initialize_collection_release_schema(connection)

    def resolve(self, alias: str) -> CollectionReleasePointer | None:
        self._ensure_open()
        normalized_alias = _normalize_name(alias, label="alias")
        with self._pool.connection() as connection:
            row = connection.execute(
                f"""
                SELECT alias, active_collection, previous_collection, generation,
                       embedding_identity, pipeline_version, record_count,
                       snapshot_sha256, updated_at
                FROM {RELEASE_POINTER_TABLE}
                WHERE alias = %s
                """,
                (normalized_alias,),
            ).fetchone()
        return None if row is None else _pointer_from_row(row)

    def publish(
        self,
        *,
        alias: str,
        target_collection: str,
        expected_generation: int,
        embedding_identity: str,
        pipeline_version: str,
        expected_record_count: int,
        expected_snapshot_sha256: str,
    ) -> CollectionReleasePointer:
        """Atomically publish a validated collection using generation CAS."""

        self._ensure_open()
        normalized_alias = _normalize_name(alias, label="alias")
        target = _normalize_name(target_collection, label="target_collection")
        identity = _normalize_text(embedding_identity, label="embedding_identity")
        version = _normalize_text(pipeline_version, label="pipeline_version")
        _validate_generation(expected_generation, allow_zero=True)
        if expected_record_count < 1:
            raise ValueError("expected_record_count must be greater than zero")
        snapshot_sha256 = _normalize_sha256(expected_snapshot_sha256)

        with self._pool.connection() as connection:
            current_row = connection.execute(
                f"""
                SELECT alias, active_collection, previous_collection, generation,
                       embedding_identity, pipeline_version, record_count,
                       snapshot_sha256, updated_at
                FROM {RELEASE_POINTER_TABLE}
                WHERE alias = %s
                FOR UPDATE
                """,
                (normalized_alias,),
            ).fetchone()
            current = None if current_row is None else _pointer_from_row(current_row)
            actual_generation = 0 if current is None else current.generation
            if actual_generation != expected_generation:
                raise CollectionReleaseConflictError(
                    f"release generation changed: expected {expected_generation}, "
                    f"found {actual_generation}"
                )
            if current is not None and current.active_collection == target:
                raise CollectionReleaseValidationError(
                    "target collection is already active; refusing a no-op release"
                )
            assert_collection_has_no_active_lease(connection, target)
            _validate_collection(
                connection,
                collection_name=target,
                embedding_identity=identity,
                pipeline_version=version,
                expected_record_count=expected_record_count,
                expected_snapshot_sha256=snapshot_sha256,
            )

            generation = actual_generation + 1
            replaced = None if current is None else current.active_collection
            if current is None:
                row = connection.execute(
                    f"""
                    INSERT INTO {RELEASE_POINTER_TABLE} (
                        alias, active_collection, previous_collection, generation,
                        embedding_identity, pipeline_version, record_count,
                        snapshot_sha256
                    ) VALUES (%s, %s, NULL, %s, %s, %s, %s, %s)
                    RETURNING alias, active_collection, previous_collection, generation,
                              embedding_identity, pipeline_version, record_count,
                              snapshot_sha256, updated_at
                    """,
                    (
                        normalized_alias,
                        target,
                        generation,
                        identity,
                        version,
                        expected_record_count,
                        snapshot_sha256,
                    ),
                ).fetchone()
            else:
                row = connection.execute(
                    f"""
                    UPDATE {RELEASE_POINTER_TABLE}
                    SET active_collection = %s,
                        previous_collection = active_collection,
                        generation = %s,
                        embedding_identity = %s,
                        pipeline_version = %s,
                        record_count = %s,
                        snapshot_sha256 = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE alias = %s AND generation = %s
                    RETURNING alias, active_collection, previous_collection, generation,
                              embedding_identity, pipeline_version, record_count,
                              snapshot_sha256, updated_at
                    """,
                    (
                        target,
                        generation,
                        identity,
                        version,
                        expected_record_count,
                        snapshot_sha256,
                        normalized_alias,
                        expected_generation,
                    ),
                ).fetchone()
            if row is None:
                raise CollectionReleaseConflictError("release pointer update lost its CAS race")
            self._insert_history(
                connection,
                alias=normalized_alias,
                generation=generation,
                action=CollectionReleaseAction.PUBLISH,
                collection_name=target,
                replaced_collection=replaced,
                embedding_identity=identity,
                pipeline_version=version,
                record_count=expected_record_count,
                snapshot_sha256=snapshot_sha256,
            )
        return _pointer_from_row(row)

    def rollback(
        self,
        *,
        alias: str,
        expected_generation: int,
    ) -> CollectionReleasePointer:
        """Atomically swap active and previous collections after revalidation."""

        self._ensure_open()
        normalized_alias = _normalize_name(alias, label="alias")
        _validate_generation(expected_generation, allow_zero=False)
        with self._pool.connection() as connection:
            current_row = connection.execute(
                f"""
                SELECT alias, active_collection, previous_collection, generation,
                       embedding_identity, pipeline_version, record_count,
                       snapshot_sha256, updated_at
                FROM {RELEASE_POINTER_TABLE}
                WHERE alias = %s
                FOR UPDATE
                """,
                (normalized_alias,),
            ).fetchone()
            if current_row is None:
                raise CollectionReleaseValidationError("release alias does not exist")
            current = _pointer_from_row(current_row)
            if current.generation != expected_generation:
                raise CollectionReleaseConflictError(
                    f"release generation changed: expected {expected_generation}, "
                    f"found {current.generation}"
                )
            if current.previous_collection is None:
                raise CollectionReleaseValidationError("release has no previous collection")
            assert_collection_has_no_active_lease(connection, current.previous_collection)

            history = connection.execute(
                f"""
                SELECT embedding_identity, pipeline_version, record_count, snapshot_sha256
                FROM {RELEASE_HISTORY_TABLE}
                WHERE alias = %s AND collection_name = %s
                ORDER BY generation DESC
                LIMIT 1
                """,
                (normalized_alias, current.previous_collection),
            ).fetchone()
            if history is None:
                raise CollectionReleaseValidationError(
                    "previous collection has no auditable release history"
                )
            identity, version, record_count, snapshot_sha256 = (
                str(history[0]),
                str(history[1]),
                int(history[2]),
                str(history[3]),
            )
            _validate_collection(
                connection,
                collection_name=current.previous_collection,
                embedding_identity=identity,
                pipeline_version=version,
                expected_record_count=record_count,
                expected_snapshot_sha256=snapshot_sha256,
            )

            generation = current.generation + 1
            row = connection.execute(
                f"""
                UPDATE {RELEASE_POINTER_TABLE}
                SET active_collection = previous_collection,
                    previous_collection = active_collection,
                    generation = %s,
                    embedding_identity = %s,
                    pipeline_version = %s,
                    record_count = %s,
                    snapshot_sha256 = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE alias = %s AND generation = %s
                RETURNING alias, active_collection, previous_collection, generation,
                          embedding_identity, pipeline_version, record_count,
                          snapshot_sha256, updated_at
                """,
                (
                    generation,
                    identity,
                    version,
                    record_count,
                    snapshot_sha256,
                    normalized_alias,
                    expected_generation,
                ),
            ).fetchone()
            if row is None:
                raise CollectionReleaseConflictError("rollback pointer update lost its CAS race")
            self._insert_history(
                connection,
                alias=normalized_alias,
                generation=generation,
                action=CollectionReleaseAction.ROLLBACK,
                collection_name=current.previous_collection,
                replaced_collection=current.active_collection,
                embedding_identity=identity,
                pipeline_version=version,
                record_count=record_count,
                snapshot_sha256=snapshot_sha256,
            )
        return _pointer_from_row(row)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_pool:
            self._pool.close()

    @staticmethod
    def _insert_history(
        connection,
        *,
        alias: str,
        generation: int,
        action: CollectionReleaseAction,
        collection_name: str,
        replaced_collection: str | None,
        embedding_identity: str,
        pipeline_version: str,
        record_count: int,
        snapshot_sha256: str,
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {RELEASE_HISTORY_TABLE} (
                alias, generation, action, collection_name, replaced_collection,
                embedding_identity, pipeline_version, record_count,
                snapshot_sha256
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                alias,
                generation,
                action.value,
                collection_name,
                replaced_collection,
                embedding_identity,
                pipeline_version,
                record_count,
                snapshot_sha256,
            ),
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("collection release manager is closed")


def _validate_collection(
    connection,
    *,
    collection_name: str,
    embedding_identity: str,
    pipeline_version: str,
    expected_record_count: int,
    expected_snapshot_sha256: str,
) -> None:
    row = connection.execute(
        f"""
        SELECT
            COUNT(*),
            COUNT(*) FILTER (
                WHERE metadata ->> %s = %s
                  AND metadata ->> %s = %s
            )
        FROM {PGVECTOR_TABLE_NAME}
        WHERE collection_name = %s
        """,
        (
            EMBEDDING_IDENTITY_METADATA_KEY,
            embedding_identity,
            INDEX_PIPELINE_VERSION_METADATA_KEY,
            pipeline_version,
            collection_name,
        ),
    ).fetchone()
    if row is None:
        raise CollectionReleaseValidationError("collection validation returned no row")
    actual_count, compatible_count = int(row[0]), int(row[1])
    if actual_count != expected_record_count:
        raise CollectionReleaseValidationError(
            f"collection record count mismatch: expected {expected_record_count}, "
            f"found {actual_count}"
        )
    if compatible_count != actual_count:
        raise CollectionReleaseValidationError(
            "collection contains incompatible embedding or pipeline metadata"
        )
    fingerprint_rows = connection.execute(
        f"""
        SELECT record_id, metadata ->> %s
        FROM {PGVECTOR_TABLE_NAME}
        WHERE collection_name = %s
        ORDER BY record_id
        """,
        (INDEX_FINGERPRINT_METADATA_KEY, collection_name),
    ).fetchall()
    actual_snapshot = index_snapshot_sha256_from_entries(
        [(str(item[0]), "" if item[1] is None else str(item[1])) for item in fingerprint_rows]
    )
    if actual_snapshot != expected_snapshot_sha256:
        raise CollectionReleaseValidationError(
            "collection snapshot SHA-256 does not match the expected corpus"
        )


def _pointer_from_row(row) -> CollectionReleasePointer:
    return CollectionReleasePointer(
        alias=str(row[0]),
        active_collection=str(row[1]),
        previous_collection=None if row[2] is None else str(row[2]),
        generation=int(row[3]),
        embedding_identity=str(row[4]),
        pipeline_version=str(row[5]),
        record_count=int(row[6]),
        snapshot_sha256=str(row[7]),
        updated_at=row[8],
    )


def _normalize_name(value: str, *, label: str) -> str:
    normalized = value.strip()
    if _NAME_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{label} must be a safe 1-96 character identifier")
    return normalized


def _normalize_text(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    if len(normalized) > 200:
        raise ValueError(f"{label} must not exceed 200 characters")
    return normalized


def _validate_generation(value: int, *, allow_zero: bool) -> None:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"expected_generation must be at least {minimum}")


def _normalize_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise ValueError("expected_snapshot_sha256 must be a 64-character hexadecimal digest")
    return normalized


__all__ = [
    "CollectionReleaseAction",
    "CollectionReleaseConflictError",
    "CollectionReleaseError",
    "CollectionReleasePointer",
    "CollectionReleaseValidationError",
    "PgVectorCollectionReleaseManager",
    "RELEASE_HISTORY_TABLE",
    "RELEASE_POINTER_TABLE",
]
