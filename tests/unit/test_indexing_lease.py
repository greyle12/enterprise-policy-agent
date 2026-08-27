from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import pytest

from app.rag.indexing_lease import (
    IndexingCollectionProtectedError,
    IndexingLeaseConflictError,
    IndexingLeaseLostError,
    LeaseGuardedPgVectorIndex,
    PgVectorIndexingLeaseManager,
)
from app.rag.pgvector_index import PgVectorIndex
from app.rag.vector_index import VectorRecord


class _Cursor:
    def __init__(self, *, one=None, rows=()) -> None:
        self._one = one
        self._rows = tuple(rows)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class _Database:
    def __init__(self) -> None:
        self.leases: dict[str, dict[str, object]] = {}
        self.releases: dict[str, tuple[str, str | None]] = {}
        self.vectors: dict[tuple[str, str], tuple[object, ...]] = {}
        self.sql: list[str] = []
        self.batch_calls = 0


class _Connection:
    def __init__(self, database: _Database) -> None:
        self.database = database

    def execute(self, query: str, params=None) -> _Cursor:
        values = tuple(params or ())
        self.database.sql.append(query)
        if "CREATE TABLE IF NOT EXISTS" in query:
            return _Cursor()
        if "INSERT INTO rag_vector_indexing_leases" in query:
            collection = str(values[0])
            self.database.leases.setdefault(
                collection,
                {
                    "owner": None,
                    "token": None,
                    "fence": 0,
                    "active": False,
                    "acquired": None,
                    "renewed": None,
                    "expires": None,
                },
            )
            return _Cursor()
        if "SELECT collection_name, owner_id, lease_token" in query:
            collection = str(values[0])
            lease = self.database.leases[collection]
            return _Cursor(one=_lease_row(collection, lease, include_active=True))
        if "FROM rag_vector_collection_releases" in query:
            collection = str(values[0])
            for alias, (active, previous) in self.database.releases.items():
                if collection in {active, previous}:
                    return _Cursor(one=(alias, active, previous))
            return _Cursor()
        if "SET owner_id = %s" in query:
            owner, token, fence, _, collection = values
            lease = self.database.leases[str(collection)]
            lease.update(
                owner=owner,
                token=token,
                fence=fence,
                active=True,
                acquired="acquired",
                renewed="renewed",
                expires="expires",
            )
            return _Cursor(one=_lease_row(str(collection), lease))
        if "SET renewed_at = CURRENT_TIMESTAMP" in query:
            _, collection, owner, token, fence = values
            lease = self.database.leases[str(collection)]
            if not _matches(lease, owner, token, fence) or not lease["active"]:
                return _Cursor()
            lease["renewed"] = "renewed-again"
            lease["expires"] = "expires-again"
            return _Cursor(one=_lease_row(str(collection), lease))
        if "SET owner_id = NULL" in query:
            collection, owner, token, fence = values
            lease = self.database.leases[str(collection)]
            if not _matches(lease, owner, token, fence):
                return _Cursor()
            lease.update(owner=None, token=None, active=False, expires=None)
            return _Cursor(one=(lease["fence"],))
        if "SELECT collection_name, owner_id, fencing_token" in query:
            collection = str(values[0])
            lease = self.database.leases.get(collection)
            if lease is None:
                return _Cursor()
            return _Cursor(
                one=(
                    collection,
                    lease["owner"],
                    lease["fence"],
                    lease["expires"],
                    lease["active"],
                )
            )
        if "SELECT fencing_token" in query and "FOR UPDATE" in query:
            collection, owner, token, fence = values
            lease = self.database.leases[str(collection)]
            if _matches(lease, owner, token, fence) and lease["active"]:
                return _Cursor(one=(fence,))
            return _Cursor()
        if "SELECT record_id, metadata" in query:
            collection = str(values[0])
            rows = [
                (record_id, json.loads(str(record[2])))
                for (stored_collection, record_id), record in self.database.vectors.items()
                if stored_collection == collection
            ]
            return _Cursor(rows=rows)
        if "SELECT COUNT(*)" in query:
            collection = str(values[0])
            count = sum(key[0] == collection for key in self.database.vectors)
            return _Cursor(one=(count,))
        return _Cursor()

    @contextmanager
    def cursor(self) -> Iterator[_BatchCursor]:
        yield _BatchCursor(self.database)


class _BatchCursor:
    def __init__(self, database: _Database) -> None:
        self.database = database

    def executemany(self, query: str, params_seq: Sequence[Sequence[object]]) -> None:
        self.database.sql.append(query)
        self.database.batch_calls += 1
        for collection, record_id, text, vector, metadata in params_seq:
            self.database.vectors[(str(collection), str(record_id))] = (text, vector, metadata)


class _Pool:
    def __init__(self, database: _Database | None = None) -> None:
        self.database = database or _Database()

    @contextmanager
    def connection(self) -> Iterator[_Connection]:
        yield _Connection(self.database)

    def close(self) -> None:
        return None


def _lease_row(collection: str, lease: dict[str, object], *, include_active: bool = False):
    row = (
        collection,
        lease["owner"],
        lease["token"],
        lease["fence"],
        lease["acquired"],
        lease["renewed"],
        lease["expires"],
    )
    return (*row, lease["active"]) if include_active else row


def _matches(lease: dict[str, object], owner, token, fence) -> bool:
    return (lease["owner"], lease["token"], lease["fence"]) == (owner, token, fence)


def _manager(database: _Database | None = None):
    pool = _Pool(database)
    manager = PgVectorIndexingLeaseManager(pool=pool)
    manager.initialize_schema()
    return manager, pool


def test_acquire_conflict_release_and_monotonic_fencing() -> None:
    manager, _ = _manager()
    first = manager.acquire(
        collection_name="policy-green",
        owner_id="builder-a",
        ttl_seconds=30,
        lease_token="a" * 32,
    )

    with pytest.raises(IndexingLeaseConflictError, match="builder-a"):
        manager.acquire(
            collection_name="policy-green",
            owner_id="builder-b",
            ttl_seconds=30,
            lease_token="b" * 32,
        )

    manager.release(first)
    second = manager.acquire(
        collection_name="policy-green",
        owner_id="builder-b",
        ttl_seconds=30,
        lease_token="b" * 32,
    )
    assert second.fencing_token == first.fencing_token + 1


def test_expired_owner_is_fenced_and_cannot_renew_or_write() -> None:
    manager, pool = _manager()
    first = manager.acquire(
        collection_name="policy-green",
        owner_id="builder-a",
        ttl_seconds=30,
        lease_token="a" * 32,
    )
    pool.database.leases["policy-green"]["active"] = False
    second = manager.acquire(
        collection_name="policy-green",
        owner_id="builder-b",
        ttl_seconds=30,
        lease_token="b" * 32,
    )

    with pytest.raises(IndexingLeaseLostError):
        manager.renew(first, ttl_seconds=30)
    with pool.connection() as connection, pytest.raises(IndexingLeaseLostError):
        manager.lock_for_write(connection, first)
    assert manager.renew(second, ttl_seconds=30).fencing_token == 2


def test_active_and_previous_release_collections_are_protected() -> None:
    database = _Database()
    database.releases["enterprise-policy"] = ("policy-blue", "policy-previous")
    manager, _ = _manager(database)

    for collection in ("policy-blue", "policy-previous"):
        with pytest.raises(IndexingCollectionProtectedError, match="enterprise-policy"):
            manager.acquire(
                collection_name=collection,
                owner_id="builder",
                ttl_seconds=30,
                lease_token="a" * 32,
            )


def test_guarded_pgvector_write_locks_fence_before_batch_mutation() -> None:
    database = _Database()
    manager, pool = _manager(database)
    index = PgVectorIndex(pool=pool, dimension=2, collection_name="policy-green")

    with manager.maintained(
        collection_name="policy-green",
        owner_id="builder",
        ttl_seconds=30,
        renew_interval_seconds=10,
    ) as session:
        guarded = LeaseGuardedPgVectorIndex(index, session)
        guarded.upsert([VectorRecord(record_id="chunk-1", text="policy", vector=[1.0, 0.0])])

    assert database.batch_calls == 1
    sql = "\n".join(database.sql)
    assert sql.index("SELECT fencing_token") < sql.index(
        "ON CONFLICT (collection_name, record_id) DO UPDATE"
    )


def test_guarded_no_op_still_validates_the_final_fence() -> None:
    database = _Database()
    manager, pool = _manager(database)
    index = PgVectorIndex(pool=pool, dimension=2, collection_name="policy-green")

    with manager.maintained(
        collection_name="policy-green",
        owner_id="builder",
        ttl_seconds=30,
        renew_interval_seconds=10,
    ) as session:
        LeaseGuardedPgVectorIndex(index, session).apply_changes([])

    assert sum("SELECT fencing_token" in query for query in database.sql) == 1
    assert database.batch_calls == 0
