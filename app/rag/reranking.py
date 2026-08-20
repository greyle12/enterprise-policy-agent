from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

DEFAULT_BGE_RERANKER_MODEL_NAME = "BAAI/bge-reranker-v2-m3"


class RerankerProviderName(StrEnum):
    """Supported runtime reranker implementations."""

    DISABLED = "disabled"
    BGE = "bge"


class RerankingProvider(ABC):
    """Cross-encoder reranker contract with one query and many documents."""

    @abstractmethod
    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        """Return one relevance score per document while preserving input order."""


class _ArrayLike(Protocol):
    def tolist(self) -> object:
        """Convert model output into Python values."""


class _CrossEncoderLike(Protocol):
    def predict(
        self,
        sentences: list[tuple[str, str]],
        *,
        batch_size: int,
        show_progress_bar: bool,
        convert_to_numpy: bool,
    ) -> _ArrayLike:
        """Score query-document pairs."""


class BGERerankingProvider(RerankingProvider):
    """Batch-first BGE cross-encoder reranking provider."""

    def __init__(
        self,
        model_name: str = DEFAULT_BGE_RERANKER_MODEL_NAME,
        device: str | None = None,
        batch_size: int = 32,
        model: _CrossEncoderLike | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be greater than zero")

        if model is None:
            from sentence_transformers import CrossEncoder

            model = CrossEncoder(model_name, device=device)

        self._model = model
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def device(self) -> str | None:
        return self._device

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be blank")

        document_list = list(documents)
        if not document_list:
            return []

        for index, document in enumerate(document_list):
            if not document.strip():
                raise ValueError(f"documents[{index}] must not be blank")

        predictions = self._model.predict(
            [(normalized_query, document) for document in document_list],
            batch_size=self._batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        raw_scores = predictions.tolist()
        if not isinstance(raw_scores, list):
            raise RuntimeError("reranker output must be a list")

        try:
            scores = [float(score) for score in raw_scores]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("reranker output must contain numeric scores") from exc

        _validate_scores(scores, expected_count=len(document_list))
        return scores


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    """A vector-retrieval candidate ready for cross-encoder reranking."""

    candidate_id: str
    text: str
    retrieval_score: float

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("candidate_id must not be blank")
        if not self.text.strip():
            raise ValueError("candidate text must not be blank")
        if not math.isfinite(self.retrieval_score):
            raise ValueError("retrieval_score must be finite")


@dataclass(frozen=True, slots=True)
class RerankedCandidate:
    """A candidate with its stable reranking position and score."""

    candidate: RerankCandidate
    rerank_score: float
    original_rank: int


def _validate_scores(scores: Sequence[float], *, expected_count: int) -> None:
    if len(scores) != expected_count:
        raise RuntimeError(
            "Reranker score count does not match candidate count: "
            f"{len(scores)} != {expected_count}"
        )
    if any(not math.isfinite(score) for score in scores):
        raise RuntimeError("reranker scores must be finite")


def build_reranked_candidates(
    candidates: Sequence[RerankCandidate],
    scores: Sequence[float],
    *,
    top_k: int | None = None,
) -> list[RerankedCandidate]:
    """Build a stable ranking from already-computed scores."""

    candidate_list = list(candidates)
    score_list = [float(score) for score in scores]
    if top_k is not None and top_k < 1:
        raise ValueError("top_k must be greater than zero")

    identifiers = [candidate.candidate_id for candidate in candidate_list]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("candidate_id values must be unique")

    _validate_scores(score_list, expected_count=len(candidate_list))
    ranked = [
        RerankedCandidate(
            candidate=candidate,
            rerank_score=score,
            original_rank=index,
        )
        for index, (candidate, score) in enumerate(zip(candidate_list, score_list, strict=True))
    ]
    ranked.sort(key=lambda result: (-result.rerank_score, result.original_rank))
    return ranked if top_k is None else ranked[:top_k]


def rerank_candidates(
    query: str,
    candidates: Sequence[RerankCandidate],
    *,
    provider: RerankingProvider,
    top_k: int | None = None,
) -> list[RerankedCandidate]:
    """Score all candidates in one provider call and return a stable ranking."""

    candidate_list = list(candidates)
    if not candidate_list:
        if top_k is not None and top_k < 1:
            raise ValueError("top_k must be greater than zero")
        return []

    scores = provider.score(query, [candidate.text for candidate in candidate_list])
    return build_reranked_candidates(candidate_list, scores, top_k=top_k)
