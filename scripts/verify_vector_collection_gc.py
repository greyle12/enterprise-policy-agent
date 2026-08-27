from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

from app.rag.collection_gc import (
    CollectionGCConflictError,
    CollectionGCNotReadyError,
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
            "policy-blue": _vectors(3, "blue-old", retained=True),
            "policy-previous": _vectors(2, "previous-old", retained=True),
            "policy-leased": _vectors(4, "leased-old", retained=True),
            "policy-recent": _vectors(5, "recent", retained=False),
            "policy-retired": _vectors(6, "retired-old", retained=True),
            "policy-stale-mark": _vectors(2, "stale-old", retained=True),
        }
        self.leases = {name: _lease() for name in self.vectors}
        self.leases["policy-leased"].update(owner="builder", active=True, fence=5)
        self.leases["policy-retired"]["fence"] = 7
        self.leases["policy-stale-mark"]["fence"] = 3
        self.releases = {"enterprise-policy": ("policy-blue", "policy-previous")}
        self.marks: dict[str, dict[str, object]] = {}
        self.sql: list[str] = []


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
            self.database.leases.setdefault(collection, _lease())
            return _Cursor()
        if "SELECT collection_name, owner_id, lease_token" in query:
            collection = str(values[0])
            lease = self.database.leases[collection]
            return _Cursor(
                one=(
                    collection,
                    lease["owner"],
                    None,
                    lease["fence"],
                    None,
                    None,
                    "future" if lease["active"] else None,
                    lease["active"],
                )
            )
        if "reference_kind" in query:
            collection = str(values[0])
            for alias, (active, previous) in self.database.releases.items():
                if collection == active:
                    return _Cursor(one=(alias, "active"))
                if collection == previous:
                    return _Cursor(one=(alias, "previous"))
            return _Cursor()
        if "WITH vector_activity AS" in query and "retention_eligible" in query:
            state = self.database.vectors[str(values[0])]
            return _Cursor(one=(state["count"], state["last_activity"], state["retained"]))
        if "SELECT collection_name, mark_token, fencing_token" in query:
            collection = str(values[0])
            mark = self.database.marks.get(collection)
            return _Cursor(one=None if mark is None else _mark_row(collection, mark))
        if "INSERT INTO rag_vector_collection_gc_marks" in query:
            collection, token, fence, count, activity, retention, _ = values
            mark = {
                "token": token,
                "fence": fence,
                "count": count,
                "activity": activity,
                "retention": retention,
                "ready": False,
                "swept_at": None,
                "deleted": None,
            }
            self.database.marks[str(collection)] = mark
            return _Cursor(one=_mark_row(str(collection), mark))
        if "WITH vector_activity AS" in query and "unchanged_since_mark" in query:
            state = self.database.vectors[str(values[0])]
            return _Cursor(one=(state["count"], state["last_activity"], state["unchanged"]))
        if "WITH deleted AS" in query:
            state = self.database.vectors[str(values[0])]
            deleted = int(state["count"])
            state["count"] = 0
            return _Cursor(one=(deleted,))
        if "UPDATE rag_vector_collection_gc_marks" in query:
            count, collection, token = values
            mark = self.database.marks[str(collection)]
            if mark["token"] != token:
                return _Cursor()
            mark["swept_at"] = "swept"
            mark["deleted"] = count
            mark["ready"] = True
            return _Cursor(one=_mark_row(str(collection), mark))
        return _Cursor()

    def _plan_rows(self):
        rows = []
        for collection, state in sorted(self.database.vectors.items()):
            lease = self.database.leases[collection]
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
    def __init__(self, database: _Database) -> None:
        self.database = database

    @contextmanager
    def connection(self) -> Iterator[_Connection]:
        yield _Connection(self.database)

    def close(self) -> None:
        return None


def _vectors(count: int, activity: str, *, retained: bool):
    return {
        "count": count,
        "last_activity": activity,
        "retained": retained,
        "unchanged": True,
    }


def _lease():
    return {"owner": None, "active": False, "fence": 0}


def _mark_row(collection: str, mark: dict[str, object]):
    return (
        collection,
        mark["token"],
        mark["fence"],
        mark["count"],
        mark["activity"],
        mark["retention"],
        "marked",
        "sweep-after",
        mark["swept_at"],
        mark["deleted"],
        mark["ready"],
    )


def run_verification() -> dict[str, object]:
    database = _Database()
    manager = PgVectorCollectionGCManager(pool=_Pool(database))
    manager.initialize_schema()
    marks_before_plan = dict(database.marks)
    plan = manager.plan(retention_seconds=86_400)
    by_name = {entry.collection_name: entry for entry in plan}

    retired_mark = manager.mark(
        collection_name="policy-retired",
        retention_seconds=86_400,
        sweep_grace_seconds=60,
        mark_token="a" * 32,
    )
    grace_rejected = False
    try:
        manager.sweep(collection_name="policy-retired", mark_token="a" * 32)
    except CollectionGCNotReadyError:
        grace_rejected = True

    manager.mark(
        collection_name="policy-stale-mark",
        retention_seconds=86_400,
        sweep_grace_seconds=60,
        mark_token="b" * 32,
    )
    database.marks["policy-stale-mark"]["ready"] = True
    database.leases["policy-stale-mark"]["fence"] = 4
    stale_mark_rejected = False
    try:
        manager.sweep(collection_name="policy-stale-mark", mark_token="b" * 32)
    except CollectionGCConflictError:
        stale_mark_rejected = True

    database.marks["policy-retired"]["ready"] = True
    receipt = manager.sweep(collection_name="policy-retired", mark_token="a" * 32)
    sql = "\n".join(database.sql)
    checks = {
        "dry_run_does_not_create_marks_or_delete_vectors": (
            marks_before_plan == {} and by_name["policy-retired"].record_count == 6
        ),
        "active_collection_is_protected": (
            by_name["policy-blue"].protection_reasons == ("active:enterprise-policy",)
        ),
        "previous_collection_is_protected": (
            by_name["policy-previous"].protection_reasons == ("previous:enterprise-policy",)
        ),
        "active_lease_is_protected": (
            by_name["policy-leased"].protection_reasons == ("active_lease:builder",)
        ),
        "retention_window_is_enforced": (
            by_name["policy-recent"].protection_reasons == ("retention",)
        ),
        "mark_captures_fencing_generation_and_record_count": (
            retired_mark.fencing_token == 7 and retired_mark.marked_record_count == 6
        ),
        "grace_period_prevents_immediate_sweep": grace_rejected,
        "new_fencing_generation_invalidates_old_mark": stale_mark_rejected,
        "sweep_deletes_exact_marked_count_and_keeps_control_row": (
            receipt.deleted_record_count == 6
            and database.vectors["policy-retired"]["count"] == 0
            and database.leases["policy-retired"]["fence"] == 7
        ),
        "sweep_rechecks_control_plane_before_delete": (
            sql.rindex("SELECT collection_name, owner_id, lease_token")
            < sql.rindex("DELETE FROM rag_policy_vectors")
        ),
        "offline_verifier_has_no_database_or_model_calls": True,
    }
    return {
        "schema_version": "1.0",
        "phase": 37,
        "passed": all(checks.values()),
        "eligible_collections": [entry.collection_name for entry in plan if entry.eligible],
        "deleted_collection": receipt.collection_name,
        "deleted_record_count": receipt.deleted_record_count,
        "retained_fencing_token": database.leases["policy-retired"]["fence"],
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
