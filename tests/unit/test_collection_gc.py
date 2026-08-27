from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from app.rag.collection_gc import (
    CollectionGCConflictError,
    CollectionGCNotReadyError,
    CollectionGCProtectedError,
    CollectionGCRetentionError,
    PgVectorCollectionGCManager,
)


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
        self.vectors = {
            "policy-active": _vector_state(3, "active-old", retained=True),
            "policy-previous": _vector_state(2, "previous-old", retained=True),
            "policy-leased": _vector_state(4, "leased-old", retained=True),
            "policy-recent": _vector_state(5, "recent", retained=False),
            "policy-retired": _vector_state(6, "retired-old", retained=True),
            "policy-stale-mark": _vector_state(2, "stale-old", retained=True),
        }
        self.leases: dict[str, dict[str, object]] = {name: _lease_state() for name in self.vectors}
        self.leases["policy-leased"].update(
            owner="builder-a",
            token="a" * 32,
            fence=4,
            active=True,
            expires="future",
        )
        self.leases["policy-retired"]["fence"] = 7
        self.leases["policy-stale-mark"]["fence"] = 3
        self.releases = {
            "enterprise-policy": ("policy-active", "policy-previous"),
        }
        self.marks: dict[str, dict[str, object]] = {}
        self.sql: list[str] = []
        self.closed = False


class _Connection:
    def __init__(self, database: _Database) -> None:
        self.database = database

    def execute(self, query: str, params=None) -> _Cursor:
        values = tuple(params or ())
        self.database.sql.append(query)
        if "CREATE TABLE IF NOT EXISTS" in query:
            return _Cursor()
        if "WITH vector_stats AS" in query:
            return _Cursor(rows=self._plan_rows())
        if "INSERT INTO rag_vector_indexing_leases" in query:
            collection = str(values[0])
            self.database.leases.setdefault(collection, _lease_state())
            return _Cursor()
        if "SELECT collection_name, owner_id, lease_token" in query:
            collection = str(values[0])
            return _Cursor(one=_lease_row(collection, self.database.leases[collection]))
        if "SELECT alias," in query and "reference_kind" in query:
            collection = str(values[0])
            for alias, (active, previous) in self.database.releases.items():
                if collection == active:
                    return _Cursor(one=(alias, "active"))
                if collection == previous:
                    return _Cursor(one=(alias, "previous"))
            return _Cursor()
        if "WITH vector_activity AS" in query and "retention_eligible" in query:
            collection = str(values[0])
            state = self.database.vectors.get(collection)
            if state is None:
                return _Cursor(one=(0, None, False))
            return _Cursor(one=(state["count"], state["last_activity"], state["retained"]))
        if "SELECT collection_name, mark_token, fencing_token" in query:
            collection = str(values[0])
            mark = self.database.marks.get(collection)
            return _Cursor(one=None if mark is None else _mark_row(collection, mark))
        if "INSERT INTO rag_vector_collection_gc_marks" in query:
            collection, token, fence, count, last_activity, retention, _ = values
            mark = {
                "token": token,
                "fence": fence,
                "count": count,
                "last_activity": last_activity,
                "retention": retention,
                "marked_at": "mark-time",
                "sweep_after": "sweep-after",
                "swept_at": None,
                "deleted_count": None,
                "ready": False,
            }
            self.database.marks[str(collection)] = mark
            return _Cursor(one=_mark_row(str(collection), mark))
        if "WITH vector_activity AS" in query and "unchanged_since_mark" in query:
            collection = str(values[0])
            state = self.database.vectors[collection]
            return _Cursor(one=(state["count"], state["last_activity"], state["unchanged"]))
        if "WITH deleted AS" in query:
            collection = str(values[0])
            state = self.database.vectors[collection]
            deleted_count = int(state["count"])
            state["count"] = 0
            return _Cursor(one=(deleted_count,))
        if "UPDATE rag_vector_collection_gc_marks" in query:
            count, collection, token = values
            mark = self.database.marks[str(collection)]
            if mark["token"] != token or mark["swept_at"] is not None:
                return _Cursor()
            mark["swept_at"] = "swept-time"
            mark["deleted_count"] = count
            mark["ready"] = True
            return _Cursor(one=_mark_row(str(collection), mark))
        return _Cursor()

    def _plan_rows(self):
        rows = []
        for collection, state in sorted(self.database.vectors.items()):
            if int(state["count"]) < 1:
                continue
            lease = self.database.leases.get(collection, _lease_state())
            active_alias = None
            previous_alias = None
            for alias, (active, previous) in self.database.releases.items():
                if collection == active:
                    active_alias = alias
                if collection == previous:
                    previous_alias = alias
            rows.append(
                (
                    collection,
                    state["count"],
                    lease["fence"],
                    state["last_activity"],
                    lease["owner"],
                    lease["active"],
                    active_alias,
                    previous_alias,
                    state["retained"],
                )
            )
        return rows


class _Pool:
    def __init__(self, database: _Database | None = None) -> None:
        self.database = database or _Database()

    @contextmanager
    def connection(self) -> Iterator[_Connection]:
        yield _Connection(self.database)

    def close(self) -> None:
        self.database.closed = True


def _vector_state(count: int, last_activity: str, *, retained: bool):
    return {
        "count": count,
        "last_activity": last_activity,
        "retained": retained,
        "unchanged": True,
    }


def _lease_state():
    return {
        "owner": None,
        "token": None,
        "fence": 0,
        "active": False,
        "acquired": None,
        "renewed": None,
        "expires": None,
        "released": None,
    }


def _lease_row(collection: str, lease: dict[str, object]):
    return (
        collection,
        lease["owner"],
        lease["token"],
        lease["fence"],
        lease["acquired"],
        lease["renewed"],
        lease["expires"],
        lease["active"],
    )


def _mark_row(collection: str, mark: dict[str, object]):
    return (
        collection,
        mark["token"],
        mark["fence"],
        mark["count"],
        mark["last_activity"],
        mark["retention"],
        mark["marked_at"],
        mark["sweep_after"],
        mark["swept_at"],
        mark["deleted_count"],
        mark["ready"],
    )


def _manager(database: _Database | None = None):
    pool = _Pool(database)
    manager = PgVectorCollectionGCManager(pool=pool)
    manager.initialize_schema()
    return manager, pool


def test_schema_initialization_creates_every_plan_dependency() -> None:
    manager, pool = _manager()

    sql = "\n".join(pool.database.sql)
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "CREATE TABLE IF NOT EXISTS rag_policy_vectors" in sql
    assert "CREATE TABLE IF NOT EXISTS rag_vector_collection_releases" in sql
    assert "CREATE TABLE IF NOT EXISTS rag_vector_indexing_leases" in sql
    assert "CREATE TABLE IF NOT EXISTS rag_vector_collection_gc_marks" in sql


def test_plan_is_read_only_and_explains_every_protection_boundary() -> None:
    manager, pool = _manager()
    before_marks = dict(pool.database.marks)

    entries = manager.plan(retention_seconds=86_400)

    by_name = {entry.collection_name: entry for entry in entries}
    assert by_name["policy-active"].protection_reasons == ("active:enterprise-policy",)
    assert by_name["policy-previous"].protection_reasons == ("previous:enterprise-policy",)
    assert by_name["policy-leased"].protection_reasons == ("active_lease:builder-a",)
    assert by_name["policy-recent"].protection_reasons == ("retention",)
    assert by_name["policy-retired"].eligible is True
    assert pool.database.marks == before_marks
    assert not any("DELETE FROM rag_policy_vectors" in sql for sql in pool.database.sql)


@pytest.mark.parametrize(
    ("collection", "error_type", "message"),
    (
        ("policy-active", CollectionGCProtectedError, "active"),
        ("policy-previous", CollectionGCProtectedError, "previous"),
        ("policy-leased", CollectionGCProtectedError, "active indexing lease"),
        ("policy-recent", CollectionGCRetentionError, "retention"),
    ),
)
def test_mark_rejects_active_previous_leased_and_recent_collections(
    collection: str,
    error_type: type[Exception],
    message: str,
) -> None:
    manager, _ = _manager()

    with pytest.raises(error_type, match=message):
        manager.mark(
            collection_name=collection,
            retention_seconds=86_400,
            sweep_grace_seconds=60,
            mark_token="a" * 32,
        )


def test_mark_then_sweep_preserves_grace_and_writes_an_auditable_receipt() -> None:
    manager, pool = _manager()
    mark = manager.mark(
        collection_name="policy-retired",
        retention_seconds=86_400,
        sweep_grace_seconds=60,
        mark_token="a" * 32,
    )

    assert mark.fencing_token == 7
    assert mark.marked_record_count == 6
    assert pool.database.vectors["policy-retired"]["count"] == 6
    with pytest.raises(CollectionGCNotReadyError, match="grace period"):
        manager.sweep(collection_name="policy-retired", mark_token="a" * 32)

    pool.database.marks["policy-retired"]["ready"] = True
    receipt = manager.sweep(collection_name="policy-retired", mark_token="a" * 32)
    retry = manager.sweep(collection_name="policy-retired", mark_token="a" * 32)

    assert receipt.swept is True
    assert receipt.deleted_record_count == 6
    assert retry == receipt
    assert pool.database.vectors["policy-retired"]["count"] == 0
    assert pool.database.leases["policy-retired"]["fence"] == 7
    assert "policy-retired" in pool.database.leases


def test_sweep_rejects_a_new_fencing_generation_after_mark() -> None:
    manager, pool = _manager()
    manager.mark(
        collection_name="policy-stale-mark",
        retention_seconds=86_400,
        sweep_grace_seconds=60,
        mark_token="b" * 32,
    )
    pool.database.marks["policy-stale-mark"]["ready"] = True
    pool.database.leases["policy-stale-mark"]["fence"] = 4

    with pytest.raises(CollectionGCConflictError, match="fencing generation"):
        manager.sweep(collection_name="policy-stale-mark", mark_token="b" * 32)

    assert pool.database.vectors["policy-stale-mark"]["count"] == 2


def test_sweep_rejects_direct_vector_change_and_wrong_mark_token() -> None:
    manager, pool = _manager()
    manager.mark(
        collection_name="policy-retired",
        retention_seconds=86_400,
        sweep_grace_seconds=60,
        mark_token="c" * 32,
    )
    pool.database.marks["policy-retired"]["ready"] = True

    with pytest.raises(CollectionGCConflictError, match="mark token"):
        manager.sweep(collection_name="policy-retired", mark_token="d" * 32)

    pool.database.vectors["policy-retired"]["count"] = 7
    pool.database.vectors["policy-retired"]["last_activity"] = "changed"
    with pytest.raises(CollectionGCConflictError, match="vectors changed"):
        manager.sweep(collection_name="policy-retired", mark_token="c" * 32)


def test_sweep_locks_control_plane_and_rechecks_references_before_delete() -> None:
    manager, pool = _manager()
    manager.mark(
        collection_name="policy-retired",
        retention_seconds=86_400,
        sweep_grace_seconds=60,
        mark_token="e" * 32,
    )
    pool.database.marks["policy-retired"]["ready"] = True
    pool.database.sql.clear()

    manager.sweep(collection_name="policy-retired", mark_token="e" * 32)

    sql = "\n".join(pool.database.sql)
    assert sql.index("SELECT collection_name, owner_id, lease_token") < sql.index("reference_kind")
    assert sql.index("reference_kind") < sql.index("DELETE FROM rag_policy_vectors")


def test_owned_pool_closes_once_and_inputs_are_bounded() -> None:
    pool = _Pool()
    manager = PgVectorCollectionGCManager(pool=pool, owns_pool=True)

    with pytest.raises(ValueError, match="retention_seconds"):
        manager.plan(retention_seconds=-1)
    with pytest.raises(ValueError, match="sweep_grace_seconds"):
        manager.mark(
            collection_name="policy-retired",
            retention_seconds=0,
            sweep_grace_seconds=1,
        )

    manager.close()
    manager.close()
    assert pool.database.closed is True
    with pytest.raises(RuntimeError, match="closed"):
        manager.plan(retention_seconds=0)
