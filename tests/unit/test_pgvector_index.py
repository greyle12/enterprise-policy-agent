from __future__ import annotations

import json
import math
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from types import ModuleType

import pytest

from app.rag.pgvector_index import PgVectorIndex
from app.rag.vector_index import VectorRecord


def _parse_vector(value: str) -> list[float]:
    return [float(item) for item in value.strip()[1:-1].split(",")]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm)


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
        self.records: dict[tuple[str, str], tuple[str, list[float], dict[str, str]]] = {}
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.executemany_calls = 0
        self.cursor_calls = 0
        self.schema_ready = False


class _Connection:
    def __init__(self, database: _Database) -> None:
        self.database = database

    def execute(self, query: str, params=None) -> _Cursor:
        normalized_params = tuple(params or ())
        self.database.executed.append((query, normalized_params))
        if "CREATE TABLE IF NOT EXISTS" in query:
            self.database.schema_ready = True
            return _Cursor()
        if "SELECT COUNT(*)" in query:
            collection = str(normalized_params[0])
            count = sum(key[0] == collection for key in self.database.records)
            return _Cursor(one=(count,))
        if "SELECT\n                    EXISTS" in query:
            return _Cursor(one=(self.database.schema_ready, self.database.schema_ready))
        if "SELECT record_id, metadata" in query:
            collection = str(normalized_params[0])
            rows = [
                (record_id, metadata)
                for (stored_collection, record_id), (_, _, metadata) in sorted(
                    self.database.records.items()
                )
                if stored_collection == collection
            ]
            return _Cursor(rows=rows)
        if "DELETE FROM rag_policy_vectors" in query:
            collection = str(normalized_params[0])
            delete_ids = set(normalized_params[1]) if "record_id = ANY" in query else None
            self.database.records = {
                key: value
                for key, value in self.database.records.items()
                if key[0] != collection or (delete_ids is not None and key[1] not in delete_ids)
            }
            return _Cursor()
        if "ORDER BY authorized_records.embedding <=>" in query:
            collection = str(normalized_params[0])
            if "record_id = ANY" in query:
                allowed = set(normalized_params[1])
                query_vector = _parse_vector(str(normalized_params[2]))
                top_k = int(normalized_params[3])
            else:
                allowed = None
                query_vector = _parse_vector(str(normalized_params[1]))
                top_k = int(normalized_params[2])
            scored = []
            for (stored_collection, record_id), (
                text,
                vector,
                metadata,
            ) in self.database.records.items():
                if stored_collection != collection:
                    continue
                if allowed is not None and record_id not in allowed:
                    continue
                scored.append(
                    (
                        record_id,
                        text,
                        "[" + ",".join(str(value) for value in vector) + "]",
                        metadata,
                        _cosine(query_vector, vector),
                    )
                )
            scored.sort(key=lambda row: row[4], reverse=True)
            return _Cursor(rows=scored[:top_k])
        return _Cursor()

    @contextmanager
    def cursor(self) -> Iterator[_BatchCursor]:
        self.database.cursor_calls += 1
        yield _BatchCursor(self.database)


class _BatchCursor:
    def __init__(self, database: _Database) -> None:
        self.database = database

    def executemany(self, query: str, params_seq: Sequence[Sequence[object]]) -> None:
        assert "ON CONFLICT (collection_name, record_id) DO UPDATE" in query
        self.database.executemany_calls += 1
        for collection, record_id, text, vector, metadata in params_seq:
            self.database.records[(str(collection), str(record_id))] = (
                str(text),
                _parse_vector(str(vector)),
                json.loads(str(metadata)),
            )


class _Pool:
    def __init__(self, database: _Database | None = None) -> None:
        self.database = database or _Database()
        self.closed = False
        self.connection_count = 0

    @contextmanager
    def connection(self) -> Iterator[_Connection]:
        self.connection_count += 1
        yield _Connection(self.database)

    def close(self) -> None:
        self.closed = True


def _records() -> list[VectorRecord]:
    return [
        VectorRecord(
            record_id="travel",
            text="住宿费凭发票报销",
            vector=[1.0, 0.0, 0.0],
            metadata={"security_level": "internal"},
        ),
        VectorRecord(
            record_id="core-secret",
            text="CORE-PGVECTOR-SECRET",
            vector=[0.99, 0.01, 0.0],
            metadata={"security_level": "core"},
        ),
        VectorRecord(
            record_id="purchase",
            text="采购审批",
            vector=[0.0, 1.0, 0.0],
        ),
    ]


def test_schema_is_idempotent_and_uses_exact_cosine_storage() -> None:
    pool = _Pool()
    index = PgVectorIndex(pool=pool, dimension=3, collection_name="policies")

    index.initialize_schema()
    index.initialize_schema()

    sql = "\n".join(query for query, _ in pool.database.executed)
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "embedding VECTOR NOT NULL" in sql
    assert "ALTER COLUMN embedding TYPE VECTOR" in sql
    assert "format_type(attribute.atttypid, attribute.atttypmod) <> 'vector'" in sql
    assert "PRIMARY KEY (collection_name, record_id)" in sql
    assert "USING hnsw" not in sql
    index.ping()


def test_upsert_persists_across_index_instances_and_updates_by_id() -> None:
    database = _Database()
    first = PgVectorIndex(pool=_Pool(database), dimension=3, collection_name="policies")
    first.initialize_schema()
    first.upsert(_records())
    assert first.size == 3
    assert database.cursor_calls == 1
    assert database.executemany_calls == 1

    second = PgVectorIndex(pool=_Pool(database), dimension=3, collection_name="policies")
    second.upsert(
        [
            VectorRecord(
                record_id="travel",
                text="住宿费需要合规票据",
                vector=[1.0, 0.0, 0.0],
                metadata={"version": "2"},
            )
        ]
    )

    assert second.size == 3
    result = second.search([1.0, 0.0, 0.0], top_k=1)
    assert result[0].record.record_id == "travel"
    assert result[0].record.text == "住宿费需要合规票据"
    assert result[0].record.metadata == {"version": "2"}
    assert result[0].score == pytest.approx(1.0)


def test_lists_entries_without_loading_embeddings() -> None:
    pool = _Pool()
    index = PgVectorIndex(pool=pool, dimension=3, collection_name="policies")
    index.upsert(_records())

    entries = index.list_entries()

    assert [entry.record_id for entry in entries] == ["core-secret", "purchase", "travel"]
    assert entries[0].metadata == {"security_level": "core"}
    query, _ = pool.database.executed[-1]
    assert "SELECT record_id, metadata" in query
    assert "embedding" not in query


def test_apply_changes_uses_one_connection_for_upsert_and_scoped_delete() -> None:
    pool = _Pool()
    index = PgVectorIndex(pool=pool, dimension=3, collection_name="policies")
    index.upsert(_records())
    before = pool.connection_count

    index.apply_changes(
        [
            VectorRecord(
                record_id="travel",
                text="更新后的差旅规则",
                vector=[1.0, 0.0, 0.0],
            )
        ],
        delete_record_ids={"purchase"},
    )

    assert pool.connection_count == before + 1
    assert index.size == 2
    delete_sql, params = next(
        (query, params)
        for query, params in reversed(pool.database.executed)
        if "record_id = ANY" in query and "DELETE" in query
    )
    assert "collection_name = %s" in delete_sql
    assert params == ("policies", ["purchase"])


def test_authorization_allow_list_is_in_sql_before_similarity_ordering() -> None:
    pool = _Pool()
    index = PgVectorIndex(pool=pool, dimension=3, collection_name="policies")
    index.initialize_schema()
    index.upsert(_records())

    results = index.search(
        [1.0, 0.0, 0.0],
        top_k=2,
        allowed_record_ids={"travel", "purchase"},
    )

    assert [result.record.record_id for result in results] == ["travel", "purchase"]
    assert all("CORE-PGVECTOR-SECRET" not in result.record.text for result in results)
    search_sql, params = next(
        (query, params)
        for query, params in reversed(pool.database.executed)
        if "ORDER BY authorized_records.embedding <=>" in query
    )
    assert search_sql.index("record_id = ANY") < search_sql.index(
        "ORDER BY authorized_records.embedding <=>"
    )
    assert "WITH authorized_records AS MATERIALIZED" in search_sql
    assert "authorized_records.embedding::text" in search_sql
    assert "query_vector.query_embedding" in search_sql
    assert set(params[1]) == {"travel", "purchase"}


def test_empty_authorization_scope_skips_database_search() -> None:
    pool = _Pool()
    index = PgVectorIndex(pool=pool, dimension=3, collection_name="policies")
    before = len(pool.database.executed)

    assert index.search([1.0, 0.0, 0.0], allowed_record_ids=set()) == []
    assert len(pool.database.executed) == before


@pytest.mark.parametrize(
    "vector",
    ([1.0, 0.0], [0.0, 0.0, 0.0], [float("nan"), 0.0, 0.0]),
)
def test_rejects_invalid_vectors(vector: list[float]) -> None:
    index = PgVectorIndex(pool=_Pool(), dimension=3, collection_name="policies")

    with pytest.raises(ValueError):
        index.search(vector)


def test_owned_pool_is_closed_once() -> None:
    pool = _Pool()
    index = PgVectorIndex(
        pool=pool,
        dimension=3,
        collection_name="policies",
        owns_pool=True,
    )

    index.close()
    index.close()

    assert pool.closed is True
    with pytest.raises(RuntimeError, match="closed"):
        index.ping()


def test_from_dsn_opens_and_waits_for_psycopg_pool(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _ConnectionPool(_Pool):
        def __init__(self, **kwargs) -> None:
            super().__init__()
            captured.update(kwargs)

        def wait(self, *, timeout: float) -> None:
            captured["wait_timeout"] = timeout

    module = ModuleType("psycopg_pool")
    module.ConnectionPool = _ConnectionPool  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "psycopg_pool", module)

    index = PgVectorIndex.from_dsn(
        "postgresql://user:secret@database.example/policies",
        dimension=3,
        collection_name="policies",
        min_pool_size=2,
        max_pool_size=6,
        connect_timeout_seconds=4.0,
    )

    assert captured == {
        "conninfo": "postgresql://user:secret@database.example/policies",
        "min_size": 2,
        "max_size": 6,
        "kwargs": {"connect_timeout": 4},
        "timeout": 4.0,
        "open": True,
        "wait_timeout": 4.0,
    }
    index.close()
