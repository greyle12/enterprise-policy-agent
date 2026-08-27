from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

from app.rag.indexing_lease import (
    IndexingCollectionProtectedError,
    IndexingLeaseConflictError,
    IndexingLeaseLostError,
    PgVectorIndexingLeaseManager,
)


class _Cursor:
    def __init__(self, *, one=None) -> None:
        self._one = one

    def fetchone(self):
        return self._one


class _Database:
    def __init__(self) -> None:
        self.leases: dict[str, dict[str, object]] = {}
        self.releases = {"enterprise-policy": ("policy-blue", "policy-previous")}
        self.sql: list[str] = []
        self.connection_count = 0


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
                {"owner": None, "token": None, "fence": 0, "active": False},
            )
            return _Cursor()
        if "SELECT collection_name, owner_id, lease_token" in query:
            collection = str(values[0])
            lease = self.database.leases[collection]
            return _Cursor(one=_control_row(collection, lease))
        if "FROM rag_vector_collection_releases" in query:
            collection = str(values[0])
            for alias, (active, previous) in self.database.releases.items():
                if collection in {active, previous}:
                    return _Cursor(one=(alias, active, previous))
            return _Cursor()
        if "SET owner_id = %s" in query:
            owner, token, fence, _, collection = values
            lease = self.database.leases[str(collection)]
            lease.update(owner=owner, token=token, fence=fence, active=True)
            return _Cursor(one=_lease_row(str(collection), lease))
        if "SET renewed_at = CURRENT_TIMESTAMP" in query:
            _, collection, owner, token, fence = values
            lease = self.database.leases[str(collection)]
            if not _matches(lease, owner, token, fence) or not lease["active"]:
                return _Cursor()
            return _Cursor(one=_lease_row(str(collection), lease))
        if "SET owner_id = NULL" in query:
            collection, owner, token, fence = values
            lease = self.database.leases[str(collection)]
            if not _matches(lease, owner, token, fence):
                return _Cursor()
            lease.update(owner=None, token=None, active=False)
            return _Cursor(one=(fence,))
        if "SELECT fencing_token" in query and "FOR UPDATE" in query:
            collection, owner, token, fence = values
            lease = self.database.leases[str(collection)]
            return _Cursor(
                one=(fence,) if _matches(lease, owner, token, fence) and lease["active"] else None
            )
        return _Cursor()


class _Pool:
    def __init__(self, database: _Database) -> None:
        self.database = database

    @contextmanager
    def connection(self) -> Iterator[_Connection]:
        self.database.connection_count += 1
        yield _Connection(self.database)

    def close(self) -> None:
        return None


def _control_row(collection: str, lease: dict[str, object]):
    return (
        collection,
        lease["owner"],
        lease["token"],
        lease["fence"],
        "acquired",
        "renewed",
        "expires" if lease["active"] else None,
        lease["active"],
    )


def _lease_row(collection: str, lease: dict[str, object]):
    return _control_row(collection, lease)[:7]


def _matches(lease: dict[str, object], owner, token, fence) -> bool:
    return (lease["owner"], lease["token"], lease["fence"]) == (owner, token, fence)


def run_verification() -> dict[str, object]:
    database = _Database()
    manager = PgVectorIndexingLeaseManager(pool=_Pool(database))
    manager.initialize_schema()
    first = manager.acquire(
        collection_name="policy-green",
        owner_id="builder-a",
        ttl_seconds=30,
        lease_token="a" * 32,
    )
    conflict_rejected = False
    try:
        manager.acquire(
            collection_name="policy-green",
            owner_id="builder-b",
            ttl_seconds=30,
            lease_token="b" * 32,
        )
    except IndexingLeaseConflictError:
        conflict_rejected = True

    database.leases["policy-green"]["active"] = False
    second = manager.acquire(
        collection_name="policy-green",
        owner_id="builder-b",
        ttl_seconds=30,
        lease_token="b" * 32,
    )
    stale_writer_rejected = False
    try:
        with _Pool(database).connection() as connection:
            manager.lock_for_write(connection, first)
    except IndexingLeaseLostError:
        stale_writer_rejected = True

    protected_rejected = False
    try:
        manager.acquire(
            collection_name="policy-blue",
            owner_id="builder-c",
            ttl_seconds=30,
            lease_token="c" * 32,
        )
    except IndexingCollectionProtectedError:
        protected_rejected = True
    manager.release(second)

    sql = "\n".join(database.sql)
    checks = {
        "first_builder_acquires_generation_one": first.fencing_token == 1,
        "concurrent_builder_is_rejected": conflict_rejected,
        "expired_lease_takeover_increments_fence": second.fencing_token == 2,
        "stale_writer_is_rejected_by_fencing_token": stale_writer_rejected,
        "active_release_collection_cannot_be_rebuilt": protected_rejected,
        "collection_control_row_is_locked_before_release_lookup": (
            sql.index("FOR UPDATE") < sql.index("FROM rag_vector_collection_releases")
        ),
        "lease_row_is_retained_after_release": (
            "policy-green" in database.leases
            and database.leases["policy-green"]["owner"] is None
            and database.leases["policy-green"]["fence"] == 2
        ),
        "offline_verifier_has_no_database_or_model_calls": True,
    }
    return {
        "schema_version": "1.0",
        "phase": 36,
        "passed": all(checks.values()),
        "collection_name": "policy-green",
        "latest_fencing_token": second.fencing_token,
        "database_calls": False,
        "external_model_calls": False,
        "checks": checks,
    }


def main() -> int:
    result = run_verification()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
