from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from math import ceil
from threading import Event, Lock, Thread
from typing import Self
from uuid import uuid4

from app.rag.pgvector_index import (
    PgVectorConnectionPool,
    PgVectorIndex,
    _ConnectionLike,
)
from app.rag.vector_index import SearchResult, VectorIndexEntry, VectorRecord

INDEXING_LEASE_TABLE = "rag_vector_indexing_leases"
RELEASE_POINTER_TABLE = "rag_vector_collection_releases"
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_TOKEN_PATTERN = re.compile(r"^[a-f0-9]{32}$")


class IndexingLeaseError(RuntimeError):
    """Base error for distributed collection build leases."""


class IndexingLeaseConflictError(IndexingLeaseError):
    """Raised when another live builder owns the collection."""


class IndexingLeaseLostError(IndexingLeaseError):
    """Raised when an expired or fenced writer attempts to renew, write, or release."""


class IndexingCollectionProtectedError(IndexingLeaseError):
    """Raised when a builder targets an active or rollback-protected collection."""


@dataclass(frozen=True, slots=True)
class IndexingLease:
    collection_name: str
    owner_id: str
    lease_token: str
    fencing_token: int
    acquired_at: object
    renewed_at: object
    expires_at: object


@dataclass(frozen=True, slots=True)
class IndexingLeaseStatus:
    collection_name: str
    owner_id: str | None
    fencing_token: int
    expires_at: object | None
    active: bool


def initialize_indexing_lease_schema(connection: _ConnectionLike) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {INDEXING_LEASE_TABLE} (
            collection_name TEXT PRIMARY KEY,
            owner_id TEXT,
            lease_token TEXT,
            fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
            acquired_at TIMESTAMPTZ,
            renewed_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ,
            released_at TIMESTAMPTZ,
            CHECK (
                (owner_id IS NULL AND lease_token IS NULL AND expires_at IS NULL)
                OR
                (owner_id IS NOT NULL AND lease_token IS NOT NULL AND expires_at IS NOT NULL)
            )
        )
        """
    )


def lock_indexing_lease_row(connection: _ConnectionLike, collection_name: str):
    """Create and lock the stable collection control row for build/publish coordination."""

    connection.execute(
        f"""
        INSERT INTO {INDEXING_LEASE_TABLE} (collection_name)
        VALUES (%s)
        ON CONFLICT (collection_name) DO NOTHING
        """,
        (collection_name,),
    )
    row = connection.execute(
        f"""
        SELECT collection_name, owner_id, lease_token, fencing_token,
               acquired_at, renewed_at, expires_at,
               (expires_at IS NOT NULL AND expires_at > CURRENT_TIMESTAMP) AS active
        FROM {INDEXING_LEASE_TABLE}
        WHERE collection_name = %s
        FOR UPDATE
        """,
        (collection_name,),
    ).fetchone()
    if row is None:
        raise RuntimeError("indexing lease control row could not be locked")
    return row


def assert_collection_has_no_active_lease(
    connection: _ConnectionLike,
    collection_name: str,
) -> None:
    row = lock_indexing_lease_row(connection, collection_name)
    if bool(row[7]):
        raise IndexingLeaseConflictError(
            f"collection has an active indexing lease owned by {row[1]}"
        )


class PgVectorIndexingLeaseManager:
    """PostgreSQL lease manager with monotonic fencing tokens per collection."""

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
            raise RuntimeError("indexing leases require the psycopg pool dependency") from exc

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
            initialize_indexing_lease_schema(connection)

    def acquire(
        self,
        *,
        collection_name: str,
        owner_id: str,
        ttl_seconds: int,
        lease_token: str | None = None,
    ) -> IndexingLease:
        self._ensure_open()
        collection = _normalize_name(collection_name, label="collection_name")
        owner = _normalize_owner(owner_id)
        ttl = _validate_ttl(ttl_seconds)
        token = _normalize_token(lease_token or uuid4().hex)
        with self._pool.connection() as connection:
            row = lock_indexing_lease_row(connection, collection)
            if bool(row[7]):
                raise IndexingLeaseConflictError(
                    f"collection has an active indexing lease owned by {row[1]}"
                )
            protected = connection.execute(
                f"""
                SELECT alias, active_collection, previous_collection
                FROM {RELEASE_POINTER_TABLE}
                WHERE active_collection = %s OR previous_collection = %s
                LIMIT 1
                """,
                (collection, collection),
            ).fetchone()
            if protected is not None:
                raise IndexingCollectionProtectedError(
                    f"collection is protected by release alias {protected[0]}"
                )
            fencing_token = int(row[3]) + 1
            updated = connection.execute(
                f"""
                UPDATE {INDEXING_LEASE_TABLE}
                SET owner_id = %s,
                    lease_token = %s,
                    fencing_token = %s,
                    acquired_at = CURRENT_TIMESTAMP,
                    renewed_at = CURRENT_TIMESTAMP,
                    expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    released_at = NULL
                WHERE collection_name = %s
                RETURNING collection_name, owner_id, lease_token, fencing_token,
                          acquired_at, renewed_at, expires_at
                """,
                (owner, token, fencing_token, ttl, collection),
            ).fetchone()
        if updated is None:
            raise RuntimeError("indexing lease acquisition returned no row")
        return _lease_from_row(updated)

    def renew(self, lease: IndexingLease, *, ttl_seconds: int) -> IndexingLease:
        self._ensure_open()
        ttl = _validate_ttl(ttl_seconds)
        with self._pool.connection() as connection:
            row = connection.execute(
                f"""
                UPDATE {INDEXING_LEASE_TABLE}
                SET renewed_at = CURRENT_TIMESTAMP,
                    expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                WHERE collection_name = %s
                  AND owner_id = %s
                  AND lease_token = %s
                  AND fencing_token = %s
                  AND expires_at > CURRENT_TIMESTAMP
                RETURNING collection_name, owner_id, lease_token, fencing_token,
                          acquired_at, renewed_at, expires_at
                """,
                (
                    ttl,
                    lease.collection_name,
                    lease.owner_id,
                    lease.lease_token,
                    lease.fencing_token,
                ),
            ).fetchone()
        if row is None:
            raise IndexingLeaseLostError("indexing lease expired or was fenced by a new owner")
        return _lease_from_row(row)

    def release(self, lease: IndexingLease) -> None:
        self._ensure_open()
        with self._pool.connection() as connection:
            row = connection.execute(
                f"""
                UPDATE {INDEXING_LEASE_TABLE}
                SET owner_id = NULL,
                    lease_token = NULL,
                    expires_at = NULL,
                    released_at = CURRENT_TIMESTAMP
                WHERE collection_name = %s
                  AND owner_id = %s
                  AND lease_token = %s
                  AND fencing_token = %s
                RETURNING fencing_token
                """,
                (
                    lease.collection_name,
                    lease.owner_id,
                    lease.lease_token,
                    lease.fencing_token,
                ),
            ).fetchone()
        if row is None:
            raise IndexingLeaseLostError("cannot release a lease owned by another generation")

    def status(self, collection_name: str) -> IndexingLeaseStatus | None:
        self._ensure_open()
        collection = _normalize_name(collection_name, label="collection_name")
        with self._pool.connection() as connection:
            row = connection.execute(
                f"""
                SELECT collection_name, owner_id, fencing_token, expires_at,
                       (expires_at IS NOT NULL AND expires_at > CURRENT_TIMESTAMP) AS active
                FROM {INDEXING_LEASE_TABLE}
                WHERE collection_name = %s
                """,
                (collection,),
            ).fetchone()
        if row is None:
            return None
        return IndexingLeaseStatus(
            collection_name=str(row[0]),
            owner_id=None if row[1] is None else str(row[1]),
            fencing_token=int(row[2]),
            expires_at=row[3],
            active=bool(row[4]),
        )

    def lock_for_write(self, connection: _ConnectionLike, lease: IndexingLease) -> None:
        row = connection.execute(
            f"""
            SELECT fencing_token
            FROM {INDEXING_LEASE_TABLE}
            WHERE collection_name = %s
              AND owner_id = %s
              AND lease_token = %s
              AND fencing_token = %s
              AND expires_at > CURRENT_TIMESTAMP
            FOR UPDATE
            """,
            (
                lease.collection_name,
                lease.owner_id,
                lease.lease_token,
                lease.fencing_token,
            ),
        ).fetchone()
        if row is None:
            raise IndexingLeaseLostError(
                "indexing write rejected because the lease expired or was fenced"
            )

    def maintained(
        self,
        *,
        collection_name: str,
        owner_id: str,
        ttl_seconds: int,
        renew_interval_seconds: float,
    ) -> MaintainedIndexingLease:
        return MaintainedIndexingLease(
            manager=self,
            collection_name=collection_name,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
            renew_interval_seconds=renew_interval_seconds,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_pool:
            self._pool.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("indexing lease manager is closed")


class MaintainedIndexingLease:
    """Context-managed lease renewed by a background heartbeat."""

    def __init__(
        self,
        *,
        manager: PgVectorIndexingLeaseManager,
        collection_name: str,
        owner_id: str,
        ttl_seconds: int,
        renew_interval_seconds: float,
    ) -> None:
        if renew_interval_seconds <= 0 or renew_interval_seconds >= ttl_seconds:
            raise ValueError("renew interval must be positive and less than lease TTL")
        self._manager = manager
        self._collection_name = collection_name
        self._owner_id = owner_id
        self._ttl_seconds = ttl_seconds
        self._renew_interval = renew_interval_seconds
        self._stop = Event()
        self._lock = Lock()
        self._lease: IndexingLease | None = None
        self._failure: BaseException | None = None
        self._thread: Thread | None = None

    @property
    def lease(self) -> IndexingLease:
        with self._lock:
            if self._lease is None:
                raise RuntimeError("indexing lease session has not started")
            return self._lease

    def __enter__(self) -> MaintainedIndexingLease:
        lease = self._manager.acquire(
            collection_name=self._collection_name,
            owner_id=self._owner_id,
            ttl_seconds=self._ttl_seconds,
        )
        with self._lock:
            self._lease = lease
        self._thread = Thread(target=self._heartbeat, name="indexing-lease-heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self._renew_interval * 2))
        failure = self._failure
        try:
            self._manager.release(self.lease)
        except IndexingLeaseLostError as release_error:
            failure = failure or release_error
        if exc is None and failure is not None:
            raise IndexingLeaseLostError("indexing lease heartbeat failed") from failure

    def require_healthy(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise IndexingLeaseLostError("indexing lease heartbeat failed") from failure

    def lock_for_write(self, connection: _ConnectionLike, collection_name: str) -> None:
        self.require_healthy()
        lease = self.lease
        if collection_name != lease.collection_name:
            raise IndexingLeaseLostError("write guard collection does not match the lease")
        self._manager.lock_for_write(connection, lease)

    def _heartbeat(self) -> None:
        while not self._stop.wait(self._renew_interval):
            try:
                renewed = self._manager.renew(self.lease, ttl_seconds=self._ttl_seconds)
            except BaseException as exc:  # noqa: BLE001 - propagate background failure to writer
                with self._lock:
                    self._failure = exc
                self._stop.set()
                return
            with self._lock:
                self._lease = renewed


class LeaseGuardedPgVectorIndex:
    """VectorIndex view whose mutations are fenced by a maintained database lease."""

    def __init__(self, index: PgVectorIndex, guard: MaintainedIndexingLease) -> None:
        if index.collection_name != guard.lease.collection_name:
            raise ValueError("pgvector index collection must match the indexing lease")
        self._index = index
        self._guard = guard

    @property
    def dimension(self) -> int:
        return self._index.dimension

    @property
    def size(self) -> int:
        return self._index.size

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        self.apply_changes(records)

    def list_entries(self) -> list[VectorIndexEntry]:
        return self._index.list_entries()

    def apply_changes(
        self,
        records: Sequence[VectorRecord],
        *,
        delete_record_ids: Collection[str] = (),
    ) -> None:
        self._index.apply_changes_guarded(
            records,
            delete_record_ids=delete_record_ids,
            write_guard=self._guard,
        )

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        *,
        allowed_record_ids: Collection[str] | None = None,
    ) -> list[SearchResult]:
        return self._index.search(
            query_vector,
            top_k,
            allowed_record_ids=allowed_record_ids,
        )

    def ping(self) -> None:
        self._index.ping()

    def close(self) -> None:
        self._index.close()


def _lease_from_row(row) -> IndexingLease:
    return IndexingLease(
        collection_name=str(row[0]),
        owner_id=str(row[1]),
        lease_token=str(row[2]),
        fencing_token=int(row[3]),
        acquired_at=row[4],
        renewed_at=row[5],
        expires_at=row[6],
    )


def _normalize_name(value: str, *, label: str) -> str:
    normalized = value.strip()
    if _NAME_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{label} must be a safe 1-96 character identifier")
    return normalized


def _normalize_owner(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise ValueError("owner_id must contain 1-200 characters")
    return normalized


def _normalize_token(value: str) -> str:
    normalized = value.strip().lower()
    if _TOKEN_PATTERN.fullmatch(normalized) is None:
        raise ValueError("lease_token must be 32 lowercase hexadecimal characters")
    return normalized


def _validate_ttl(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 86_400:
        raise ValueError("ttl_seconds must be between 5 and 86400")
    return value


__all__ = [
    "INDEXING_LEASE_TABLE",
    "IndexingCollectionProtectedError",
    "IndexingLease",
    "IndexingLeaseConflictError",
    "IndexingLeaseError",
    "IndexingLeaseLostError",
    "IndexingLeaseStatus",
    "LeaseGuardedPgVectorIndex",
    "MaintainedIndexingLease",
    "PgVectorIndexingLeaseManager",
    "assert_collection_has_no_active_lease",
    "initialize_indexing_lease_schema",
    "lock_indexing_lease_row",
]
