from __future__ import annotations

import re
from dataclasses import dataclass
from math import ceil
from typing import Self
from uuid import uuid4

from app.rag.collection_release import (
    RELEASE_POINTER_TABLE,
    initialize_collection_release_schema,
)
from app.rag.indexing_lease import (
    INDEXING_LEASE_TABLE,
    lock_indexing_lease_row,
)
from app.rag.pgvector_index import (
    PGVECTOR_TABLE_NAME,
    PgVectorConnectionPool,
    _ConnectionLike,
    initialize_pgvector_schema,
)

COLLECTION_GC_MARK_TABLE = "rag_vector_collection_gc_marks"
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_TOKEN_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_MAX_RETENTION_SECONDS = 31_536_000
_MAX_SWEEP_GRACE_SECONDS = 604_800


class CollectionGCError(RuntimeError):
    """Base error for safe vector collection garbage collection."""


class CollectionGCProtectedError(CollectionGCError):
    """Raised when a release pointer or live indexing lease protects a collection."""


class CollectionGCRetentionError(CollectionGCError):
    """Raised when a collection has not aged beyond the configured retention window."""


class CollectionGCConflictError(CollectionGCError):
    """Raised when collection state changed after the mark phase."""


class CollectionGCNotReadyError(CollectionGCError):
    """Raised when sweep is attempted before the mark grace period expires."""


@dataclass(frozen=True, slots=True)
class CollectionGCPlanEntry:
    collection_name: str
    record_count: int
    fencing_token: int
    last_activity_at: object
    protection_reasons: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return not self.protection_reasons

    def to_dict(self) -> dict[str, object]:
        return {
            "collection_name": self.collection_name,
            "record_count": self.record_count,
            "fencing_token": self.fencing_token,
            "last_activity_at": str(self.last_activity_at),
            "eligible": self.eligible,
            "protection_reasons": list(self.protection_reasons),
        }


@dataclass(frozen=True, slots=True)
class CollectionGCMark:
    collection_name: str
    mark_token: str
    fencing_token: int
    marked_record_count: int
    last_activity_at: object
    retention_seconds: int
    marked_at: object
    sweep_after: object
    swept_at: object | None
    deleted_record_count: int | None
    ready: bool

    @property
    def swept(self) -> bool:
        return self.swept_at is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "collection_name": self.collection_name,
            "mark_token": self.mark_token,
            "fencing_token": self.fencing_token,
            "marked_record_count": self.marked_record_count,
            "last_activity_at": str(self.last_activity_at),
            "retention_seconds": self.retention_seconds,
            "marked_at": str(self.marked_at),
            "sweep_after": str(self.sweep_after),
            "swept_at": None if self.swept_at is None else str(self.swept_at),
            "deleted_record_count": self.deleted_record_count,
            "ready": self.ready,
            "swept": self.swept,
        }


def initialize_collection_gc_schema(connection: _ConnectionLike) -> None:
    initialize_pgvector_schema(connection)
    initialize_collection_release_schema(connection)
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {COLLECTION_GC_MARK_TABLE} (
            collection_name TEXT PRIMARY KEY,
            mark_token TEXT NOT NULL CHECK (mark_token ~ '^[0-9a-f]{{32}}$'),
            fencing_token BIGINT NOT NULL CHECK (fencing_token >= 0),
            marked_record_count INTEGER NOT NULL CHECK (marked_record_count > 0),
            last_activity_at TIMESTAMPTZ NOT NULL,
            retention_seconds INTEGER NOT NULL CHECK (retention_seconds >= 0),
            marked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sweep_after TIMESTAMPTZ NOT NULL,
            swept_at TIMESTAMPTZ,
            deleted_record_count INTEGER CHECK (deleted_record_count >= 0)
        )
        """
    )


class PgVectorCollectionGCManager:
    """Two-phase collection GC coordinated by release pointers and lease fences."""

    def __init__(self, *, pool: PgVectorConnectionPool, owns_pool: bool = False) -> None:
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
            raise RuntimeError("collection GC requires the psycopg pool dependency") from exc

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
            initialize_collection_gc_schema(connection)

    def plan(self, *, retention_seconds: int) -> tuple[CollectionGCPlanEntry, ...]:
        """Return a read-only deletion plan; this method never marks or deletes data."""

        self._ensure_open()
        retention = _validate_retention(retention_seconds)
        with self._pool.connection() as connection:
            rows = connection.execute(
                f"""
                WITH vector_stats AS (
                    SELECT collection_name,
                           COUNT(*)::INTEGER AS record_count,
                           MAX(updated_at) AS vector_updated_at
                    FROM {PGVECTOR_TABLE_NAME}
                    GROUP BY collection_name
                ), collection_state AS (
                    SELECT stats.collection_name,
                           stats.record_count,
                           COALESCE(lease.fencing_token, 0) AS fencing_token,
                           GREATEST(
                               stats.vector_updated_at,
                               lease.acquired_at,
                               lease.renewed_at,
                               lease.released_at
                           ) AS last_activity_at,
                           lease.owner_id,
                           lease.expires_at,
                           (
                               SELECT MIN(alias)
                               FROM {RELEASE_POINTER_TABLE}
                               WHERE active_collection = stats.collection_name
                           ) AS active_alias,
                           (
                               SELECT MIN(alias)
                               FROM {RELEASE_POINTER_TABLE}
                               WHERE previous_collection = stats.collection_name
                           ) AS previous_alias
                    FROM vector_stats AS stats
                    LEFT JOIN {INDEXING_LEASE_TABLE} AS lease
                      ON lease.collection_name = stats.collection_name
                )
                SELECT collection_name, record_count, fencing_token, last_activity_at,
                       owner_id,
                       (expires_at IS NOT NULL AND expires_at > CURRENT_TIMESTAMP),
                       active_alias,
                       previous_alias,
                       (
                           last_activity_at <=
                           CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                       ) AS retention_eligible
                FROM collection_state
                ORDER BY collection_name
                """,
                (retention,),
            ).fetchall()
        return tuple(_plan_entry_from_row(row) for row in rows)

    def mark(
        self,
        *,
        collection_name: str,
        retention_seconds: int,
        sweep_grace_seconds: int,
        mark_token: str | None = None,
    ) -> CollectionGCMark:
        """Mark one unprotected, retained collection without deleting vectors."""

        self._ensure_open()
        collection = _normalize_name(collection_name)
        retention = _validate_retention(retention_seconds)
        grace = _validate_grace(sweep_grace_seconds)
        token = _normalize_token(mark_token or uuid4().hex)
        with self._pool.connection() as connection:
            lease_row = lock_indexing_lease_row(connection, collection)
            _require_inactive_lease(lease_row)
            _require_unreferenced(connection, collection)
            record_count, last_activity_at = _require_retained_collection(
                connection,
                collection_name=collection,
                retention_seconds=retention,
            )
            existing = _select_mark(connection, collection, for_update=True)
            if (
                existing is not None
                and not existing.swept
                and existing.fencing_token == int(lease_row[3])
                and existing.marked_record_count == record_count
                and existing.last_activity_at == last_activity_at
            ):
                return existing
            row = connection.execute(
                f"""
                INSERT INTO {COLLECTION_GC_MARK_TABLE} (
                    collection_name, mark_token, fencing_token,
                    marked_record_count, last_activity_at, retention_seconds,
                    marked_at, sweep_after, swept_at, deleted_record_count
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    NULL, NULL
                )
                ON CONFLICT (collection_name) DO UPDATE SET
                    mark_token = EXCLUDED.mark_token,
                    fencing_token = EXCLUDED.fencing_token,
                    marked_record_count = EXCLUDED.marked_record_count,
                    last_activity_at = EXCLUDED.last_activity_at,
                    retention_seconds = EXCLUDED.retention_seconds,
                    marked_at = EXCLUDED.marked_at,
                    sweep_after = EXCLUDED.sweep_after,
                    swept_at = NULL,
                    deleted_record_count = NULL
                RETURNING collection_name, mark_token, fencing_token,
                          marked_record_count, last_activity_at, retention_seconds,
                          marked_at, sweep_after, swept_at, deleted_record_count,
                          (sweep_after <= CURRENT_TIMESTAMP) AS ready
                """,
                (
                    collection,
                    token,
                    int(lease_row[3]),
                    record_count,
                    last_activity_at,
                    retention,
                    grace,
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("collection GC mark returned no row")
        return _mark_from_row(row)

    def status(self, collection_name: str) -> CollectionGCMark | None:
        self._ensure_open()
        collection = _normalize_name(collection_name)
        with self._pool.connection() as connection:
            return _select_mark(connection, collection, for_update=False)

    def sweep(self, *, collection_name: str, mark_token: str) -> CollectionGCMark:
        """Delete a marked collection after rechecking every safety boundary."""

        self._ensure_open()
        collection = _normalize_name(collection_name)
        token = _normalize_token(mark_token)
        with self._pool.connection() as connection:
            lease_row = lock_indexing_lease_row(connection, collection)
            mark = _select_mark(connection, collection, for_update=True)
            if mark is None:
                raise CollectionGCConflictError("collection has no GC mark")
            if mark.mark_token != token:
                raise CollectionGCConflictError("GC mark token does not match")
            if mark.swept:
                return mark
            _require_inactive_lease(lease_row)
            _require_unreferenced(connection, collection)
            if int(lease_row[3]) != mark.fencing_token:
                raise CollectionGCConflictError("collection fencing generation changed after mark")
            if not mark.ready:
                raise CollectionGCNotReadyError("GC mark grace period has not expired")
            state = connection.execute(
                f"""
                WITH vector_activity AS (
                    SELECT COUNT(*)::INTEGER AS record_count,
                           MAX(updated_at) AS vector_updated_at
                    FROM {PGVECTOR_TABLE_NAME}
                    WHERE collection_name = %s
                ), collection_activity AS (
                    SELECT activity.record_count,
                           activity.vector_updated_at,
                           GREATEST(
                               activity.vector_updated_at,
                               lease.acquired_at,
                               lease.renewed_at,
                               lease.released_at
                           ) AS last_activity_at
                    FROM vector_activity AS activity
                    LEFT JOIN {INDEXING_LEASE_TABLE} AS lease
                      ON lease.collection_name = %s
                )
                SELECT record_count, last_activity_at,
                       (vector_updated_at <= %s) AS unchanged_since_mark
                FROM collection_activity
                """,
                (collection, collection, mark.marked_at),
            ).fetchone()
            if state is None:
                raise RuntimeError("collection GC state query returned no row")
            if (
                int(state[0]) != mark.marked_record_count
                or state[1] != mark.last_activity_at
                or not bool(state[2])
            ):
                raise CollectionGCConflictError("collection vectors changed after mark")
            deleted = connection.execute(
                f"""
                WITH deleted AS (
                    DELETE FROM {PGVECTOR_TABLE_NAME}
                    WHERE collection_name = %s
                    RETURNING 1
                )
                SELECT COUNT(*)::INTEGER FROM deleted
                """,
                (collection,),
            ).fetchone()
            if deleted is None or int(deleted[0]) != mark.marked_record_count:
                raise CollectionGCConflictError("deleted record count does not match GC mark")
            row = connection.execute(
                f"""
                UPDATE {COLLECTION_GC_MARK_TABLE}
                SET swept_at = CURRENT_TIMESTAMP,
                    deleted_record_count = %s
                WHERE collection_name = %s
                  AND mark_token = %s
                  AND swept_at IS NULL
                RETURNING collection_name, mark_token, fencing_token,
                          marked_record_count, last_activity_at, retention_seconds,
                          marked_at, sweep_after, swept_at, deleted_record_count,
                          TRUE AS ready
                """,
                (mark.marked_record_count, collection, token),
            ).fetchone()
        if row is None:
            raise CollectionGCConflictError("GC receipt update lost its compare-and-set race")
        return _mark_from_row(row)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_pool:
            self._pool.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("collection GC manager is closed")


def _select_mark(
    connection: _ConnectionLike,
    collection_name: str,
    *,
    for_update: bool,
) -> CollectionGCMark | None:
    suffix = " FOR UPDATE" if for_update else ""
    row = connection.execute(
        f"""
        SELECT collection_name, mark_token, fencing_token,
               marked_record_count, last_activity_at, retention_seconds,
               marked_at, sweep_after, swept_at, deleted_record_count,
               (sweep_after <= CURRENT_TIMESTAMP) AS ready
        FROM {COLLECTION_GC_MARK_TABLE}
        WHERE collection_name = %s{suffix}
        """,
        (collection_name,),
    ).fetchone()
    return None if row is None else _mark_from_row(row)


def _require_inactive_lease(lease_row) -> None:
    if bool(lease_row[7]):
        raise CollectionGCProtectedError(
            f"collection has an active indexing lease owned by {lease_row[1]}"
        )


def _require_unreferenced(connection: _ConnectionLike, collection_name: str) -> None:
    row = connection.execute(
        f"""
        SELECT alias,
               CASE
                   WHEN active_collection = %s THEN 'active'
                   ELSE 'previous'
               END AS reference_kind
        FROM {RELEASE_POINTER_TABLE}
        WHERE active_collection = %s OR previous_collection = %s
        ORDER BY alias
        LIMIT 1
        """,
        (collection_name, collection_name, collection_name),
    ).fetchone()
    if row is not None:
        raise CollectionGCProtectedError(
            f"collection is protected as {row[1]} by release alias {row[0]}"
        )


def _require_retained_collection(
    connection: _ConnectionLike,
    *,
    collection_name: str,
    retention_seconds: int,
) -> tuple[int, object]:
    row = connection.execute(
        f"""
        WITH vector_activity AS (
            SELECT COUNT(*)::INTEGER AS record_count,
                   MAX(updated_at) AS vector_updated_at
            FROM {PGVECTOR_TABLE_NAME}
            WHERE collection_name = %s
        ), collection_activity AS (
            SELECT activity.record_count,
                   GREATEST(
                       activity.vector_updated_at,
                       lease.acquired_at,
                       lease.renewed_at,
                       lease.released_at
                   ) AS last_activity_at
            FROM vector_activity AS activity
            LEFT JOIN {INDEXING_LEASE_TABLE} AS lease
              ON lease.collection_name = %s
        )
        SELECT record_count, last_activity_at,
               (
                   last_activity_at <=
                   CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
               ) AS retention_eligible
        FROM collection_activity
        """,
        (collection_name, collection_name, retention_seconds),
    ).fetchone()
    if row is None or int(row[0]) < 1:
        raise CollectionGCConflictError("collection is empty or does not exist")
    if not bool(row[2]):
        raise CollectionGCRetentionError("collection is still inside the retention window")
    return int(row[0]), row[1]


def _plan_entry_from_row(row) -> CollectionGCPlanEntry:
    reasons: list[str] = []
    if bool(row[5]):
        reasons.append(f"active_lease:{row[4]}")
    if row[6] is not None:
        reasons.append(f"active:{row[6]}")
    if row[7] is not None:
        reasons.append(f"previous:{row[7]}")
    if not bool(row[8]):
        reasons.append("retention")
    return CollectionGCPlanEntry(
        collection_name=str(row[0]),
        record_count=int(row[1]),
        fencing_token=int(row[2]),
        last_activity_at=row[3],
        protection_reasons=tuple(reasons),
    )


def _mark_from_row(row) -> CollectionGCMark:
    return CollectionGCMark(
        collection_name=str(row[0]),
        mark_token=str(row[1]),
        fencing_token=int(row[2]),
        marked_record_count=int(row[3]),
        last_activity_at=row[4],
        retention_seconds=int(row[5]),
        marked_at=row[6],
        sweep_after=row[7],
        swept_at=row[8],
        deleted_record_count=None if row[9] is None else int(row[9]),
        ready=bool(row[10]),
    )


def _normalize_name(value: str) -> str:
    normalized = value.strip()
    if _NAME_PATTERN.fullmatch(normalized) is None:
        raise ValueError("collection_name must be a safe 1-96 character identifier")
    return normalized


def _normalize_token(value: str) -> str:
    normalized = value.strip().lower()
    if _TOKEN_PATTERN.fullmatch(normalized) is None:
        raise ValueError("mark_token must be 32 lowercase hexadecimal characters")
    return normalized


def _validate_retention(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_RETENTION_SECONDS
    ):
        raise ValueError("retention_seconds must be between 0 and 31536000")
    return value


def _validate_grace(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 5 <= value <= _MAX_SWEEP_GRACE_SECONDS
    ):
        raise ValueError("sweep_grace_seconds must be between 5 and 604800")
    return value


__all__ = [
    "COLLECTION_GC_MARK_TABLE",
    "CollectionGCConflictError",
    "CollectionGCError",
    "CollectionGCMark",
    "CollectionGCNotReadyError",
    "CollectionGCPlanEntry",
    "CollectionGCProtectedError",
    "CollectionGCRetentionError",
    "PgVectorCollectionGCManager",
    "initialize_collection_gc_schema",
]
