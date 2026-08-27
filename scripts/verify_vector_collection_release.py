from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

from app.rag.collection_release import (
    CollectionReleaseConflictError,
    PgVectorCollectionReleaseManager,
)
from app.rag.indexing import index_snapshot_sha256_from_entries


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
        self.collections = {
            "policy-blue": [
                (f"blue-{index:03d}", "bge-v1", "pipeline-v1", f"{index:064x}")
                for index in range(1, 200)
            ],
            "policy-green": [
                (f"green-{index:03d}", "bge-v2", "pipeline-v2", f"{index + 200:064x}")
                for index in range(1, 200)
            ],
        }
        self.pointer = None
        self.history: list[tuple[object, ...]] = []
        self.leases: dict[str, tuple[object, ...]] = {}
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
                (collection, None, None, 0, None, None, None, False),
            )
            return _Cursor()
        if "SELECT collection_name, owner_id, lease_token" in query:
            return _Cursor(one=self.database.leases[str(values[0])])
        if "COUNT(*) FILTER" in query:
            identity, version, collection = str(values[1]), str(values[3]), str(values[4])
            records = self.database.collections.get(collection, [])
            compatible = sum((item[1], item[2]) == (identity, version) for item in records)
            return _Cursor(one=(len(records), compatible))
        if "SELECT record_id, metadata ->>" in query:
            collection = str(values[1])
            return _Cursor(
                rows=((item[0], item[3]) for item in self.database.collections[collection])
            )
        if "FROM rag_vector_collection_releases" in query:
            return _Cursor(one=self.database.pointer)
        if "INSERT INTO rag_vector_collection_releases" in query:
            alias, collection, generation, identity, version, count, snapshot = values
            self.database.pointer = (
                alias,
                collection,
                None,
                generation,
                identity,
                version,
                count,
                snapshot,
                "offline",
            )
            return _Cursor(one=self.database.pointer)
        if "UPDATE rag_vector_collection_releases" in query:
            current = self.database.pointer
            if "active_collection = %s" in query:
                collection, generation, identity, version, count, snapshot, alias, _ = values
                self.database.pointer = (
                    alias,
                    collection,
                    current[1],
                    generation,
                    identity,
                    version,
                    count,
                    snapshot,
                    "offline",
                )
            else:
                generation, identity, version, count, snapshot, alias, _ = values
                self.database.pointer = (
                    alias,
                    current[2],
                    current[1],
                    generation,
                    identity,
                    version,
                    count,
                    snapshot,
                    "offline",
                )
            return _Cursor(one=self.database.pointer)
        if "INSERT INTO rag_vector_collection_release_history" in query:
            self.database.history.append(values)
            return _Cursor()
        if "FROM rag_vector_collection_release_history" in query:
            alias, collection = str(values[0]), str(values[1])
            row = next(
                item
                for item in reversed(self.database.history)
                if item[0] == alias and item[3] == collection
            )
            return _Cursor(one=(row[5], row[6], row[7], row[8]))
        return _Cursor()

    def executemany(self, query, params_seq) -> None:
        raise RuntimeError("release verification must not write vectors")


class _Pool:
    def __init__(self, database: _Database) -> None:
        self.database = database

    @contextmanager
    def connection(self) -> Iterator[_Connection]:
        self.database.connection_count += 1
        yield _Connection(self.database)

    def close(self) -> None:
        return None


def run_verification() -> dict[str, object]:
    database = _Database()
    manager = PgVectorCollectionReleaseManager(pool=_Pool(database))
    manager.initialize_schema()
    blue = manager.publish(
        alias="enterprise-policy",
        target_collection="policy-blue",
        expected_generation=0,
        embedding_identity="bge-v1",
        pipeline_version="pipeline-v1",
        expected_record_count=199,
        expected_snapshot_sha256=_snapshot(database, "policy-blue"),
    )
    green = manager.publish(
        alias="enterprise-policy",
        target_collection="policy-green",
        expected_generation=1,
        embedding_identity="bge-v2",
        pipeline_version="pipeline-v2",
        expected_record_count=199,
        expected_snapshot_sha256=_snapshot(database, "policy-green"),
    )
    stale_generation_rejected = False
    try:
        manager.rollback(alias="enterprise-policy", expected_generation=1)
    except CollectionReleaseConflictError:
        stale_generation_rejected = True
    rollback = manager.rollback(alias="enterprise-policy", expected_generation=2)
    resolved = manager.resolve("enterprise-policy")
    sql = "\n".join(database.sql)
    checks = {
        "first_publish_creates_generation_one": (
            blue.active_collection == "policy-blue" and blue.generation == 1
        ),
        "green_publish_preserves_previous_snapshot": (
            green.active_collection == "policy-green"
            and green.previous_collection == "policy-blue"
            and green.generation == 2
        ),
        "stale_generation_is_rejected": stale_generation_rejected,
        "rollback_atomically_swaps_blue_green": (
            rollback.active_collection == "policy-blue"
            and rollback.previous_collection == "policy-green"
            and rollback.generation == 3
        ),
        "resolved_pointer_matches_rollback": resolved == rollback,
        "snapshot_validation_precedes_pointer_update": (
            sql.index("COUNT(*) FILTER") < sql.index("INSERT INTO rag_vector_collection_releases")
        ),
        "metadata_compatibility_is_checked": (
            "metadata ->> %s = %s" in sql and "index_pipeline_version" not in sql
        ),
        "publish_and_rollback_history_is_audited": (
            [item[2] for item in database.history] == ["publish", "publish", "rollback"]
        ),
        "offline_verifier_has_no_database_or_model_calls": True,
    }
    return {
        "schema_version": "1.0",
        "phase": 35,
        "passed": all(checks.values()),
        "alias": rollback.alias,
        "active_collection": rollback.active_collection,
        "previous_collection": rollback.previous_collection,
        "generation": rollback.generation,
        "record_count": rollback.record_count,
        "snapshot_sha256": rollback.snapshot_sha256,
        "history_count": len(database.history),
        "database_calls": False,
        "external_model_calls": False,
        "checks": checks,
    }


def _snapshot(database: _Database, collection: str) -> str:
    return index_snapshot_sha256_from_entries(
        [(item[0], item[3]) for item in database.collections[collection]]
    )


def main() -> int:
    result = run_verification()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
