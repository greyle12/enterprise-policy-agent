from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from app.rag.pgvector_hnsw_experiment import PgVectorHnswExperimentIndex


class _Cursor:
    def __init__(self, *, one=None, rows=()) -> None:
        self._one = one
        self._rows = tuple(rows)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.copied_count = 2

    def execute(self, query: str, params=None) -> _Cursor:
        values = tuple(params or ())
        self.executed.append((query, values))
        if "SELECT COUNT(*)" in query:
            return _Cursor(one=(self.copied_count,))
        if "SELECT to_regclass" in query:
            return _Cursor(one=("experiment-table",))
        if "ORDER BY embedding <=>" in query:
            return _Cursor(
                rows=(
                    (
                        "travel",
                        "住宿发票",
                        "[1,0,0]",
                        {"security_level": "internal"},
                        0.99,
                    ),
                )
            )
        return _Cursor()

    def executemany(self, query, params_seq) -> None:
        raise AssertionError("experiment index is read-only")


class _Pool:
    def __init__(self) -> None:
        self.connection_object = _Connection()
        self.closed = False

    @contextmanager
    def connection(self) -> Iterator[_Connection]:
        yield self.connection_object

    def close(self) -> None:
        self.closed = True


def _prepared_index() -> tuple[PgVectorHnswExperimentIndex, _Pool]:
    pool = _Pool()
    index = PgVectorHnswExperimentIndex(
        pool=pool,
        dimension=3,
        source_collection="policies",
        experiment_id="abcdef123456",
    )
    index.prepare(
        authorized_record_ids={"travel", "purchase"},
        m=8,
        ef_construction=32,
        ef_search=20,
    )
    return index, pool


def test_materializes_authorization_before_building_hnsw() -> None:
    index, pool = _prepared_index()

    sql = "\n".join(query for query, _ in pool.connection_object.executed)
    assert sql.index("record_id = ANY") < sql.index("USING hnsw")
    assert "CREATE UNLOGGED TABLE rag_policy_hnsw_exp_abcdef123456" in sql
    assert "WITH (m = 8, ef_construction = 32)" in sql
    assert index.size == 2
    assert [entry.record_id for entry in index.list_entries()] == ["purchase", "travel"]


def test_hnsw_search_requires_the_materialized_identity_scope() -> None:
    index, pool = _prepared_index()

    results = index.search(
        [1.0, 0.0, 0.0],
        top_k=1,
        allowed_record_ids={"travel", "purchase"},
    )

    assert results[0].record.record_id == "travel"
    assert results[0].score == pytest.approx(0.99)
    assert any(
        "set_config('hnsw.ef_search'" in query for query, _ in pool.connection_object.executed
    )
    assert any(
        "SET LOCAL enable_seqscan = off" in query for query, _ in pool.connection_object.executed
    )

    with pytest.raises(ValueError, match="must match"):
        index.search([1.0, 0.0, 0.0], allowed_record_ids={"travel"})


def test_rejects_invalid_hnsw_parameters_and_incomplete_copy() -> None:
    index = PgVectorHnswExperimentIndex(
        pool=_Pool(),
        dimension=3,
        source_collection="policies",
        experiment_id="abcdef123456",
    )
    with pytest.raises(ValueError, match="twice m"):
        index.prepare(
            authorized_record_ids={"travel"},
            m=16,
            ef_construction=16,
            ef_search=20,
        )

    pool = _Pool()
    pool.connection_object.copied_count = 1
    incomplete = PgVectorHnswExperimentIndex(
        pool=pool,
        dimension=3,
        source_collection="policies",
        experiment_id="fedcba654321",
    )
    with pytest.raises(RuntimeError, match="scope is incomplete"):
        incomplete.prepare(
            authorized_record_ids={"travel", "purchase"},
            m=8,
            ef_construction=32,
            ef_search=20,
        )


def test_cleanup_drops_only_the_generated_experiment_table() -> None:
    index, pool = _prepared_index()

    index.close()

    drop_queries = [query for query, _ in pool.connection_object.executed if "DROP TABLE" in query]
    assert drop_queries == ["DROP TABLE IF EXISTS rag_policy_hnsw_exp_abcdef123456"]
    assert pool.closed is False
