from collections.abc import Sequence
from dataclasses import dataclass, field
from math import sqrt

from app.rag.embeddings import EmbeddingVector


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
        prepared_records: list[_StoredRecord] = []
        incoming_ids: set[str] = set()

        for record in records:
            if not record.record_id.strip():
                raise ValueError("record_id must not be empty")

            if (
                record.record_id in self._records
                or record.record_id in incoming_ids
            ):
                raise ValueError(
                    f"record id already exists: {record.record_id}"
                )

            self._validate_vector(record.vector)

            vector = tuple(record.vector)
            norm = _calculate_norm(vector)

            if norm == 0.0:
                raise ValueError("zero vectors cannot be indexed")

            stored_record = VectorRecord(
                record_id=record.record_id,
                text=record.text,
                vector=list(vector),
                metadata=dict(record.metadata),
            )

            prepared_records.append(
                _StoredRecord(
                    record=stored_record,
                    vector=vector,
                    norm=norm,
                )
            )
            incoming_ids.add(record.record_id)

        for stored_record in prepared_records:
            self._records[stored_record.record.record_id] = stored_record

    def search(
        self,
        query_vector: EmbeddingVector,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """返回与查询向量最相似的 Top-K 记录。"""
        if top_k < 1:
            raise ValueError("top_k must be greater than zero")

        self._validate_vector(query_vector)

        query_norm = _calculate_norm(query_vector)

        if query_norm == 0.0:
            raise ValueError("query vector must not be a zero vector")

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
        ]

        results.sort(
            key=lambda result: result.score,
            reverse=True,
        )

        return results[:top_k]

    def _validate_vector(self, vector: Sequence[float]) -> None:
        if len(vector) != self._dimension:
            raise ValueError(
                "vector dimension mismatch: "
                f"expected {self._dimension}, got {len(vector)}"
            )


def _calculate_norm(vector: Sequence[float]) -> float:
    return sqrt(sum(value * value for value in vector))


def _calculate_dot_product(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    return sum(
        left_value * right_value
        for left_value, right_value in zip(left, right, strict=True)
    )