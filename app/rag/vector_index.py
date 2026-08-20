from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite, sqrt
from typing import Protocol

from app.rag.embeddings import EmbeddingVector


class VectorStoreProviderName(StrEnum):
    """Supported vector storage backends."""

    MEMORY = "memory"
    PGVECTOR = "pgvector"


@dataclass(frozen=True)
class VectorRecord:
    """存储在向量索引中的一条文本记录。"""

    record_id: str
    text: str
    vector: EmbeddingVector
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    """一次向量检索返回的结果。"""

    record: VectorRecord
    score: float


@dataclass(frozen=True)
class VectorIndexEntry:
    """Lightweight persisted record descriptor used by incremental indexing."""

    record_id: str
    metadata: dict[str, str] = field(default_factory=dict)


class VectorIndex(Protocol):
    """Storage-independent vector index contract used by PolicyRetriever."""

    @property
    def dimension(self) -> int:
        """Return the embedding dimension accepted by this index."""

    @property
    def size(self) -> int:
        """Return the number of records in the active collection."""

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        """Insert or replace records atomically by record ID."""

    def list_entries(self) -> list[VectorIndexEntry]:
        """Return record IDs and metadata without loading stored embeddings."""

    def apply_changes(
        self,
        records: Sequence[VectorRecord],
        *,
        delete_record_ids: Collection[str] = (),
    ) -> None:
        """Atomically apply validated upserts and deletions."""

    def search(
        self,
        query_vector: EmbeddingVector,
        top_k: int = 5,
        *,
        allowed_record_ids: Collection[str] | None = None,
    ) -> list[SearchResult]:
        """Search only inside an optional pre-authorized record scope."""

    def ping(self) -> None:
        """Raise when the storage backend is unavailable."""

    def close(self) -> None:
        """Release resources owned by the index."""


@dataclass(frozen=True)
class _StoredRecord:
    """索引内部使用的不可变向量记录。"""

    record: VectorRecord
    vector: tuple[float, ...]
    norm: float


class InMemoryVectorIndex:
    """使用余弦相似度进行检索的内存向量索引。"""

    def __init__(self, dimension: int) -> None:
        if dimension < 1:
            raise ValueError("dimension must be greater than zero")

        self._dimension = dimension
        self._records: dict[str, _StoredRecord] = {}

    @property
    def dimension(self) -> int:
        """返回索引要求的向量维度。"""
        return self._dimension

    @property
    def size(self) -> int:
        """返回索引中的记录数量。"""
        return len(self._records)

    def add(self, records: Sequence[VectorRecord]) -> None:
        """向索引中添加多条向量记录。"""
        prepared_records = self._prepare_records(records)
        for stored_record in prepared_records:
            if stored_record.record.record_id in self._records:
                raise ValueError(f"record id already exists: {stored_record.record.record_id}")

        for stored_record in prepared_records:
            self._records[stored_record.record.record_id] = stored_record

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        """Insert or replace records while preserving index validation."""

        self.apply_changes(records)

    def list_entries(self) -> list[VectorIndexEntry]:
        """Return deterministic lightweight descriptors for synchronization."""

        return [
            VectorIndexEntry(
                record_id=stored.record.record_id,
                metadata=dict(stored.record.metadata),
            )
            for stored in sorted(
                self._records.values(),
                key=lambda item: item.record.record_id,
            )
        ]

    def apply_changes(
        self,
        records: Sequence[VectorRecord],
        *,
        delete_record_ids: Collection[str] = (),
    ) -> None:
        """Validate the whole change set before replacing the in-memory snapshot."""

        prepared = self._prepare_records(records)
        delete_ids = _normalize_delete_ids(delete_record_ids)
        upsert_ids = {stored.record.record_id for stored in prepared}
        overlap = sorted(upsert_ids.intersection(delete_ids))
        if overlap:
            raise ValueError(
                "record ids cannot be upserted and deleted together: " + ", ".join(overlap)
            )

        updated = dict(self._records)
        for stored_record in prepared:
            updated[stored_record.record.record_id] = stored_record
        for record_id in delete_ids:
            updated.pop(record_id, None)
        self._records = updated

    def search(
        self,
        query_vector: EmbeddingVector,
        top_k: int = 5,
        *,
        allowed_record_ids: Collection[str] | None = None,
    ) -> list[SearchResult]:
        """返回过滤范围内与查询向量最相似的 Top-K 记录。"""
        if top_k < 1:
            raise ValueError("top_k must be greater than zero")

        self._validate_vector(query_vector)

        query_norm = _calculate_norm(query_vector)

        if query_norm == 0.0:
            raise ValueError("query vector must not be a zero vector")

        allowed = frozenset(allowed_record_ids) if allowed_record_ids is not None else None
        results = [
            SearchResult(
                record=stored_record.record,
                score=(
                    _calculate_dot_product(
                        query_vector,
                        stored_record.vector,
                    )
                    / (query_norm * stored_record.norm)
                ),
            )
            for stored_record in self._records.values()
            if allowed is None or stored_record.record.record_id in allowed
        ]

        results.sort(
            key=lambda result: result.score,
            reverse=True,
        )

        return results[:top_k]

    def _validate_vector(self, vector: Sequence[float]) -> None:
        if len(vector) != self._dimension:
            raise ValueError(
                f"vector dimension mismatch: expected {self._dimension}, got {len(vector)}"
            )
        if any(not isfinite(value) for value in vector):
            raise ValueError("vectors must contain only finite values")

    def _prepare_records(self, records: Sequence[VectorRecord]) -> list[_StoredRecord]:
        prepared_records: list[_StoredRecord] = []
        incoming_ids: set[str] = set()
        for record in records:
            if not record.record_id.strip():
                raise ValueError("record_id must not be empty")
            if record.record_id in incoming_ids:
                raise ValueError(f"record id already exists in batch: {record.record_id}")

            self._validate_vector(record.vector)
            vector = tuple(record.vector)
            norm = _calculate_norm(vector)
            if norm == 0.0:
                raise ValueError("zero vectors cannot be indexed")

            prepared_records.append(
                _StoredRecord(
                    record=VectorRecord(
                        record_id=record.record_id,
                        text=record.text,
                        vector=list(vector),
                        metadata=dict(record.metadata),
                    ),
                    vector=vector,
                    norm=norm,
                )
            )
            incoming_ids.add(record.record_id)
        return prepared_records

    def ping(self) -> None:
        """The process-local store is ready while its object exists."""

    def close(self) -> None:
        """The process-local store owns no external resources."""


def _calculate_norm(vector: Sequence[float]) -> float:
    return sqrt(sum(value * value for value in vector))


def _calculate_dot_product(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    return sum(
        left_value * right_value for left_value, right_value in zip(left, right, strict=True)
    )


def _normalize_delete_ids(record_ids: Collection[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for record_id in record_ids:
        value = record_id.strip()
        if not value:
            raise ValueError("delete record ids must not be blank")
        normalized.add(value)
    return frozenset(normalized)
