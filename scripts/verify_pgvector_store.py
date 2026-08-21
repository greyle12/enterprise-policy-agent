from __future__ import annotations

import json
import math
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import yaml

from app.rag.pgvector_index import PgVectorIndex
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_retriever import PolicyRetriever
from app.schemas.policy import SecurityLevel
from app.security import PolicyAccessContext, TrustedIdentitySource

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_AS_OF_DATE = date(2026, 8, 20)


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


class _OfflineDatabase:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], tuple[str, list[float], dict[str, str]]] = {}
        self.sql: list[str] = []
        self.last_search_params: tuple[object, ...] = ()
        self.scored_record_ids: list[str] = []
        self.schema_ready = False
        self.batch_calls = 0


class _OfflineConnection:
    def __init__(self, database: _OfflineDatabase) -> None:
        self._database = database

    def execute(self, query: str, params=None) -> _Cursor:
        values = tuple(params or ())
        self._database.sql.append(query)
        if "CREATE TABLE IF NOT EXISTS" in query:
            self._database.schema_ready = True
            return _Cursor()
        if "SELECT COUNT(*)" in query:
            collection = str(values[0])
            count = sum(key[0] == collection for key in self._database.records)
            return _Cursor(one=(count,))
        if "SELECT\n                    EXISTS" in query:
            return _Cursor(one=(self._database.schema_ready, self._database.schema_ready))
        if "ORDER BY embedding <=>" not in query:
            return _Cursor()

        self._database.last_search_params = values
        collection = str(values[0])
        allowed = set(values[1])
        query_vector = _parse_vector(str(values[2]))
        top_k = int(values[3])
        rows = []
        self._database.scored_record_ids = []
        for (stored_collection, record_id), (
            text,
            vector,
            metadata,
        ) in self._database.records.items():
            if stored_collection != collection or record_id not in allowed:
                continue
            self._database.scored_record_ids.append(record_id)
            rows.append(
                (
                    record_id,
                    text,
                    "[" + ",".join(str(value) for value in vector) + "]",
                    metadata,
                    _cosine(query_vector, vector),
                )
            )
        rows.sort(key=lambda row: row[4], reverse=True)
        return _Cursor(rows=rows[:top_k])

    def executemany(self, query: str, params_seq: Sequence[Sequence[object]]) -> None:
        if "ON CONFLICT (collection_name, record_id) DO UPDATE" not in query:
            raise RuntimeError("offline database received an unexpected write")
        self._database.batch_calls += 1
        for collection, record_id, text, vector, metadata in params_seq:
            self._database.records[(str(collection), str(record_id))] = (
                str(text),
                _parse_vector(str(vector)),
                json.loads(str(metadata)),
            )


class _OfflinePool:
    def __init__(self, database: _OfflineDatabase) -> None:
        self._database = database

    @contextmanager
    def connection(self) -> Iterator[_OfflineConnection]:
        yield _OfflineConnection(self._database)

    def close(self) -> None:
        return None


class _EmbeddingProvider:
    @property
    def dimension(self) -> int:
        return 3

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0, 0.0] if "PGVECTOR-CORE-SECRET" in text else [0.8, 0.2, 0.0] for text in texts
        ]

    def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("text must not be blank")
        return [1.0, 0.0, 0.0]


def _access_context() -> PolicyAccessContext:
    return PolicyAccessContext(
        employee_id="PGVECTOR-VERIFY-001",
        department="演示部门",
        roles=("EMPLOYEE",),
        security_clearance=SecurityLevel.INTERNAL,
        region="中国大陆",
        identity_source=TrustedIdentitySource.TEST_FIXTURE,
    )


def run_verification() -> dict[str, object]:
    """Verify the pgvector adapter, persistence, and SQL authorization contract offline."""

    base_chunk = chunk_policy_directory(_POLICY_DIRECTORY)[0]
    unauthorized = base_chunk.model_copy(
        update={
            "chunk_id": "phase29-core-secret",
            "retrieval_text": "核心制度 PGVECTOR-CORE-SECRET",
            "content": "核心制度 PGVECTOR-CORE-SECRET",
            "security_level": SecurityLevel.CORE,
        }
    )
    authorized = base_chunk.model_copy(
        update={
            "chunk_id": "phase29-authorized",
            "retrieval_text": "内部差旅住宿制度",
            "content": "内部差旅住宿制度",
            "security_level": SecurityLevel.INTERNAL,
        }
    )

    database = _OfflineDatabase()
    first_index = PgVectorIndex(
        pool=_OfflinePool(database),
        dimension=3,
        collection_name="phase29-policy-v1",
    )
    first_index.initialize_schema()
    raw_retriever = PolicyRetriever(
        embedding_provider=_EmbeddingProvider(),
        chunks=[unauthorized, authorized],
        vector_index=first_index,
    )
    retriever = raw_retriever.restrict(_access_context(), as_of_date=_AS_OF_DATE)
    results = retriever.search("住宿", top_k=2)

    second_index = PgVectorIndex(
        pool=_OfflinePool(database),
        dimension=3,
        collection_name="phase29-policy-v1",
    )
    second_index.ping()
    persisted_size = second_index.size

    search_sql = next(query for query in reversed(database.sql) if "ORDER BY" in query)
    compose = yaml.safe_load((_PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    postgres = compose["services"]["postgres"]
    checks = {
        "existing_retriever_uses_vector_store_protocol": raw_retriever.size == 2,
        "schema_initialization_is_idempotent_and_exact": (
            database.schema_ready
            and "CREATE EXTENSION IF NOT EXISTS vector" in "\n".join(database.sql)
            and "USING hnsw" not in "\n".join(database.sql)
        ),
        "batch_upsert_is_idempotent_storage_boundary": database.batch_calls == 1,
        "records_survive_new_index_instance": persisted_size == 2,
        "authorization_is_in_sql_before_vector_ordering": (
            search_sql.index("record_id = ANY") < search_sql.index("ORDER BY")
            and "WITH authorized_records AS MATERIALIZED" in search_sql
            and set(database.last_search_params[1]) == {"phase29-authorized"}
        ),
        "unauthorized_record_is_never_scored": (
            database.scored_record_ids == ["phase29-authorized"]
            and [result.chunk.chunk_id for result in results] == ["phase29-authorized"]
        ),
        "compose_uses_persistent_pgvector_volume": (
            postgres["image"] == "pgvector/pgvector:0.8.6-pg17-bookworm"
            and postgres["volumes"][0]["source"] == "pgvector_data"
        ),
    }
    return {
        "schema_version": "1.0",
        "phase": 29,
        "passed": all(checks.values()),
        "runtime_provider": "offline_pgvector_contract",
        "database_calls": False,
        "network_calls": False,
        "configured_image": postgres["image"],
        "collection_name": second_index.collection_name,
        "embedding_dimension": second_index.dimension,
        "persisted_record_count": persisted_size,
        "search_mode": "exact_cosine",
        "checks": checks,
    }


def main() -> int:
    try:
        report = run_verification()
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        report = {
            "schema_version": "1.0",
            "phase": 29,
            "passed": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
