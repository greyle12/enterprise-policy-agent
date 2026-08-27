from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from app.rag.collection_release import (
    CollectionReleaseConflictError,
    CollectionReleaseValidationError,
    PgVectorCollectionReleaseManager,
)
from app.rag.indexing import index_snapshot_sha256_from_entries
from app.rag.indexing_lease import IndexingLeaseConflictError


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
        self.collections: dict[str, list[tuple[str, str, str, str]]] = {
            "policy-blue": [
                ("blue-1", "bge-v1", "pipeline-v1", "1" * 64),
                ("blue-2", "bge-v1", "pipeline-v1", "2" * 64),
            ],
            "policy-green": [
                ("green-1", "bge-v2", "pipeline-v2", "3" * 64),
                ("green-2", "bge-v2", "pipeline-v2", "4" * 64),
                ("green-3", "bge-v2", "pipeline-v2", "5" * 64),
            ],
            "policy-broken": [("broken", "wrong", "pipeline-v2", "6" * 64)],
        }
        self.pointers: dict[str, tuple[object, ...]] = {}
        self.history: list[tuple[object, ...]] = []
        self.leases: dict[str, tuple[object, ...]] = {}
        self.executed: list[tuple[str, tuple[object, ...]]] = []


class _Connection:
    def __init__(self, database: _Database) -> None:
        self.database = database

    def execute(self, query: str, params=None) -> _Cursor:
        values = tuple(params or ())
        self.database.executed.append((query, values))
        if "CREATE TABLE IF NOT EXISTS" in query:
            return _Cursor()
        if "INSERT INTO rag_vector_indexing_leases" in query:
            collection = str(values[0])
            self.database.leases.setdefault(
                collection,
                (collection, None, None, 0, None, None, None, False),
            )
            return _Cursor()
        if "SELECT collection_name, owner_id, lease_token" in query:
            return _Cursor(one=self.database.leases[str(values[0])])
        if "COUNT(*) FILTER" in query:
            identity = str(values[1])
            version = str(values[3])
            collection = str(values[4])
            rows = self.database.collections.get(collection, [])
            compatible = sum((item[1], item[2]) == (identity, version) for item in rows)
            return _Cursor(one=(len(rows), compatible))
        if "SELECT record_id, metadata ->>" in query:
            collection = str(values[1])
            return _Cursor(
                rows=tuple((item[0], item[3]) for item in self.database.collections[collection])
            )
        if "FROM rag_vector_collection_releases" in query:
            return _Cursor(one=self.database.pointers.get(str(values[0])))
        if "INSERT INTO rag_vector_collection_releases" in query:
            alias, collection, generation, identity, version, count, snapshot = values
            row = (
                alias,
                collection,
                None,
                generation,
                identity,
                version,
                count,
                snapshot,
                "now",
            )
            self.database.pointers[str(alias)] = row
            return _Cursor(one=row)
        if "UPDATE rag_vector_collection_releases" in query:
            if "active_collection = %s" in query:
                collection, generation, identity, version, count, snapshot, alias, _ = values
                current = self.database.pointers[str(alias)]
                row = (
                    alias,
                    collection,
                    current[1],
                    generation,
                    identity,
                    version,
                    count,
                    snapshot,
                    "now",
                )
            else:
                generation, identity, version, count, snapshot, alias, _ = values
                current = self.database.pointers[str(alias)]
                row = (
                    alias,
                    current[2],
                    current[1],
                    generation,
                    identity,
                    version,
                    count,
                    snapshot,
                    "now",
                )
            self.database.pointers[str(alias)] = row
            return _Cursor(one=row)
        if "INSERT INTO rag_vector_collection_release_history" in query:
            self.database.history.append(values)
            return _Cursor()
        if "FROM rag_vector_collection_release_history" in query:
            alias, collection = str(values[0]), str(values[1])
            matching = [
                item for item in self.database.history if item[0] == alias and item[3] == collection
            ]
            if not matching:
                return _Cursor()
            latest = matching[-1]
            return _Cursor(one=(latest[5], latest[6], latest[7], latest[8]))
        return _Cursor()

    def executemany(self, query, params_seq) -> None:
        raise AssertionError("release manager does not use executemany")


class _Pool:
    def __init__(self) -> None:
        self.database = _Database()
        self.connection_count = 0
        self.closed = False

    @contextmanager
    def connection(self) -> Iterator[_Connection]:
        self.connection_count += 1
        yield _Connection(self.database)

    def close(self) -> None:
        self.closed = True


def _manager() -> tuple[PgVectorCollectionReleaseManager, _Pool]:
    pool = _Pool()
    manager = PgVectorCollectionReleaseManager(pool=pool)
    manager.initialize_schema()
    return manager, pool


def _publish_blue(manager: PgVectorCollectionReleaseManager):
    return manager.publish(
        alias="enterprise-policy",
        target_collection="policy-blue",
        expected_generation=0,
        embedding_identity="bge-v1",
        pipeline_version="pipeline-v1",
        expected_record_count=2,
        expected_snapshot_sha256=_snapshot("policy-blue"),
    )


def _snapshot(collection: str) -> str:
    records = _Database().collections[collection]
    return index_snapshot_sha256_from_entries([(item[0], item[3]) for item in records])


def test_publish_validates_snapshot_and_atomically_advances_pointer() -> None:
    manager, pool = _manager()
    before_connections = pool.connection_count

    pointer = _publish_blue(manager)

    assert pool.connection_count == before_connections + 1
    assert pointer.active_collection == "policy-blue"
    assert pointer.previous_collection is None
    assert pointer.generation == 1
    assert pool.database.history[0][2] == "publish"
    sql = "\n".join(query for query, _ in pool.database.executed)
    assert sql.index("COUNT(*) FILTER") < sql.index("INSERT INTO rag_vector_collection_releases")


def test_second_publish_and_rollback_swap_blue_green_with_cas() -> None:
    manager, pool = _manager()
    _publish_blue(manager)
    green = manager.publish(
        alias="enterprise-policy",
        target_collection="policy-green",
        expected_generation=1,
        embedding_identity="bge-v2",
        pipeline_version="pipeline-v2",
        expected_record_count=3,
        expected_snapshot_sha256=_snapshot("policy-green"),
    )

    assert green.active_collection == "policy-green"
    assert green.previous_collection == "policy-blue"
    assert green.generation == 2

    rolled_back = manager.rollback(alias="enterprise-policy", expected_generation=2)

    assert rolled_back.active_collection == "policy-blue"
    assert rolled_back.previous_collection == "policy-green"
    assert rolled_back.generation == 3
    assert pool.database.history[-1][2] == "rollback"


def test_stale_generation_and_incompatible_collection_are_rejected() -> None:
    manager, pool = _manager()
    _publish_blue(manager)

    with pytest.raises(CollectionReleaseConflictError, match="generation changed"):
        manager.publish(
            alias="enterprise-policy",
            target_collection="policy-green",
            expected_generation=0,
            embedding_identity="bge-v2",
            pipeline_version="pipeline-v2",
            expected_record_count=3,
            expected_snapshot_sha256=_snapshot("policy-green"),
        )

    with pytest.raises(CollectionReleaseValidationError, match="incompatible"):
        manager.publish(
            alias="enterprise-policy",
            target_collection="policy-broken",
            expected_generation=1,
            embedding_identity="bge-v2",
            pipeline_version="pipeline-v2",
            expected_record_count=1,
            expected_snapshot_sha256=_snapshot("policy-broken"),
        )
    assert pool.database.pointers["enterprise-policy"][1] == "policy-blue"


def test_same_count_but_stale_snapshot_digest_is_rejected() -> None:
    manager, pool = _manager()

    with pytest.raises(CollectionReleaseValidationError, match="snapshot SHA-256"):
        manager.publish(
            alias="enterprise-policy",
            target_collection="policy-green",
            expected_generation=0,
            embedding_identity="bge-v2",
            pipeline_version="pipeline-v2",
            expected_record_count=3,
            expected_snapshot_sha256="a" * 64,
        )

    assert pool.database.pointers == {}


def test_publish_rejects_collection_with_active_indexing_lease() -> None:
    manager, pool = _manager()
    pool.database.leases["policy-blue"] = (
        "policy-blue",
        "builder-a",
        "a" * 32,
        1,
        "acquired",
        "renewed",
        "expires",
        True,
    )

    with pytest.raises(IndexingLeaseConflictError, match="builder-a"):
        _publish_blue(manager)

    assert pool.database.pointers == {}


def test_resolve_and_rollback_require_a_published_previous_snapshot() -> None:
    manager, _ = _manager()
    assert manager.resolve("enterprise-policy") is None
    _publish_blue(manager)

    assert manager.resolve("enterprise-policy").active_collection == "policy-blue"
    with pytest.raises(CollectionReleaseValidationError, match="no previous"):
        manager.rollback(alias="enterprise-policy", expected_generation=1)


def test_owned_pool_closes_once() -> None:
    pool = _Pool()
    manager = PgVectorCollectionReleaseManager(pool=pool, owns_pool=True)

    manager.close()
    manager.close()

    assert pool.closed is True
    with pytest.raises(RuntimeError, match="closed"):
        manager.resolve("enterprise-policy")
