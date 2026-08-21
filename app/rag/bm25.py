from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from math import isfinite, log
import re
from typing import Protocol
import unicodedata

DEFAULT_BM25_K1 = 1.2
DEFAULT_BM25_B = 0.75
DEFAULT_MAX_QUERY_TOKENS = 64
DEFAULT_MAX_DOCUMENT_TOKENS = 20_000

_TOKEN_SEGMENT_PATTERN = re.compile(r"[a-z0-9]+(?:[._/-][a-z0-9]+)*|[\u3400-\u4dbf\u4e00-\u9fff]+")
_CJK_PATTERN = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")


class BM25UnsearchableQueryError(ValueError):
    """Raised when a nonblank query produces no lexical terms."""


class KeywordTokenizer(Protocol):
    """Convert policy or query text into deterministic lexical terms."""

    def tokenize(self, text: str) -> tuple[str, ...]:
        """Return normalized terms while preserving term frequency."""


class PolicyKeywordTokenizer:
    """Dependency-free tokenizer for CJK text and enterprise identifiers."""

    def tokenize(self, text: str) -> tuple[str, ...]:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        tokens: list[str] = []
        for match in _TOKEN_SEGMENT_PATTERN.finditer(normalized):
            segment = match.group(0)
            if _CJK_PATTERN.fullmatch(segment) is None:
                tokens.append(segment)
                continue
            if len(segment) <= 8:
                tokens.append(segment)
            if len(segment) == 1:
                if not tokens or tokens[-1] != segment:
                    tokens.append(segment)
                continue
            tokens.extend(segment[index : index + 2] for index in range(len(segment) - 1))
        return tuple(tokens)


@dataclass(frozen=True, slots=True)
class BM25Record:
    """One text record stored in the in-memory lexical index."""

    record_id: str
    text: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BM25SearchResult:
    """One ranked lexical match."""

    record: BM25Record
    score: float


@dataclass(frozen=True, slots=True)
class _StoredBM25Record:
    record: BM25Record
    term_frequencies: Counter[str]
    length: int


class InMemoryBM25Index:
    """Small deterministic BM25 index with authorization-scoped statistics."""

    def __init__(
        self,
        *,
        tokenizer: KeywordTokenizer | None = None,
        k1: float = DEFAULT_BM25_K1,
        b: float = DEFAULT_BM25_B,
        max_query_tokens: int = DEFAULT_MAX_QUERY_TOKENS,
        max_document_tokens: int = DEFAULT_MAX_DOCUMENT_TOKENS,
    ) -> None:
        if not isfinite(k1) or k1 < 0:
            raise ValueError("k1 must be finite and greater than or equal to zero")
        if not isfinite(b) or not 0.0 <= b <= 1.0:
            raise ValueError("b must be finite and between zero and one")
        if max_query_tokens < 1 or max_document_tokens < 1:
            raise ValueError("BM25 token limits must be greater than zero")
        self._tokenizer = tokenizer or PolicyKeywordTokenizer()
        self._k1 = k1
        self._b = b
        self._max_query_tokens = max_query_tokens
        self._max_document_tokens = max_document_tokens
        self._records: dict[str, _StoredBM25Record] = {}

    @property
    def size(self) -> int:
        return len(self._records)

    @property
    def k1(self) -> float:
        return self._k1

    @property
    def b(self) -> float:
        return self._b

    def add(self, records: Sequence[BM25Record]) -> None:
        prepared: list[_StoredBM25Record] = []
        incoming_ids: set[str] = set()
        for record in records:
            if not record.record_id.strip():
                raise ValueError("record_id must not be blank")
            if record.record_id in self._records or record.record_id in incoming_ids:
                raise ValueError(f"record id already exists: {record.record_id}")
            tokens = self._tokenizer.tokenize(record.text)
            if not tokens:
                raise ValueError(f"BM25 record contains no searchable terms: {record.record_id}")
            if len(tokens) > self._max_document_tokens:
                raise ValueError(
                    f"BM25 record exceeds token limit: {len(tokens)} > {self._max_document_tokens}"
                )
            stored_record = BM25Record(
                record_id=record.record_id,
                text=record.text,
                metadata=dict(record.metadata),
            )
            prepared.append(
                _StoredBM25Record(
                    record=stored_record,
                    term_frequencies=Counter(tokens),
                    length=len(tokens),
                )
            )
            incoming_ids.add(record.record_id)
        for record in prepared:
            self._records[record.record.record_id] = record

    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        allowed_record_ids: Collection[str] | None = None,
    ) -> list[BM25SearchResult]:
        if not query.strip():
            raise ValueError("query must not be blank")
        if top_k < 1:
            raise ValueError("top_k must be greater than zero")

        query_tokens = tuple(dict.fromkeys(self._tokenizer.tokenize(query)))
        if not query_tokens:
            raise BM25UnsearchableQueryError("query contains no searchable terms")
        if len(query_tokens) > self._max_query_tokens:
            raise ValueError(
                f"query exceeds BM25 token limit: {len(query_tokens)} > {self._max_query_tokens}"
            )

        allowed = frozenset(allowed_record_ids) if allowed_record_ids is not None else None
        candidates = tuple(
            record
            for record in self._records.values()
            if allowed is None or record.record.record_id in allowed
        )
        if not candidates:
            return []

        average_document_length = sum(record.length for record in candidates) / len(candidates)
        document_frequencies = {
            token: sum(token in record.term_frequencies for record in candidates)
            for token in query_tokens
        }
        results = [
            BM25SearchResult(
                record=record.record,
                score=self._score(
                    record,
                    query_tokens=query_tokens,
                    candidate_count=len(candidates),
                    average_document_length=average_document_length,
                    document_frequencies=document_frequencies,
                ),
            )
            for record in candidates
        ]
        results = [result for result in results if result.score > 0.0]
        results.sort(key=lambda result: (-result.score, result.record.record_id))
        return results[:top_k]

    def _score(
        self,
        record: _StoredBM25Record,
        *,
        query_tokens: tuple[str, ...],
        candidate_count: int,
        average_document_length: float,
        document_frequencies: dict[str, int],
    ) -> float:
        length_normalization = 1.0 - self._b + self._b * (record.length / average_document_length)
        score = 0.0
        for token in query_tokens:
            term_frequency = record.term_frequencies.get(token, 0)
            if term_frequency == 0:
                continue
            document_frequency = document_frequencies[token]
            inverse_document_frequency = log(
                1.0 + (candidate_count - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            saturation = (
                term_frequency
                * (self._k1 + 1.0)
                / (term_frequency + self._k1 * length_normalization)
            )
            score += inverse_document_frequency * saturation
        return score


__all__ = [
    "BM25Record",
    "BM25SearchResult",
    "BM25UnsearchableQueryError",
    "DEFAULT_BM25_B",
    "DEFAULT_BM25_K1",
    "DEFAULT_MAX_DOCUMENT_TOKENS",
    "DEFAULT_MAX_QUERY_TOKENS",
    "InMemoryBM25Index",
    "KeywordTokenizer",
    "PolicyKeywordTokenizer",
]
