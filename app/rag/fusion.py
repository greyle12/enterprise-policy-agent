from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_RRF_RANK_CONSTANT = 60


@dataclass(frozen=True, slots=True)
class RankedList:
    """One named retrieval channel ordered from most to least relevant."""

    source: str
    record_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        normalized_source = self.source.strip()
        if not normalized_source:
            raise ValueError("ranked-list source must not be blank")
        normalized_ids = tuple(record_id.strip() for record_id in self.record_ids)
        if any(not record_id for record_id in normalized_ids):
            raise ValueError("ranked-list record IDs must not be blank")
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError(
                f"ranked-list record IDs must be unique within source: {normalized_source}"
            )
        object.__setattr__(self, "source", normalized_source)
        object.__setattr__(self, "record_ids", normalized_ids)


@dataclass(frozen=True, slots=True)
class RRFContribution:
    """One channel's rank and reciprocal contribution for a record."""

    source: str
    rank: int
    score: float


@dataclass(frozen=True, slots=True)
class RRFResult:
    """One deduplicated result from reciprocal-rank fusion."""

    record_id: str
    score: float
    contributions: tuple[RRFContribution, ...]


def reciprocal_rank_fusion(
    ranked_lists: Sequence[RankedList],
    *,
    rank_constant: int = DEFAULT_RRF_RANK_CONSTANT,
    top_k: int | None = None,
) -> list[RRFResult]:
    """Fuse two or more rankings without comparing their raw score scales."""

    if isinstance(rank_constant, bool) or not isinstance(rank_constant, int) or rank_constant < 1:
        raise ValueError("rank_constant must be an integer greater than zero")
    if top_k is not None and (isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1):
        raise ValueError("top_k must be greater than zero")

    rankings = tuple(ranked_lists)
    if len(rankings) < 2:
        raise ValueError("reciprocal rank fusion requires at least two ranked lists")
    sources = [ranking.source for ranking in rankings]
    if len(set(sources)) != len(sources):
        raise ValueError("ranked-list sources must be unique")

    contributions_by_id: dict[str, list[RRFContribution]] = {}
    for ranking in rankings:
        for rank, record_id in enumerate(ranking.record_ids, start=1):
            contribution = RRFContribution(
                source=ranking.source,
                rank=rank,
                score=1.0 / (rank_constant + rank),
            )
            contributions_by_id.setdefault(record_id, []).append(contribution)

    results = [
        RRFResult(
            record_id=record_id,
            score=sum(contribution.score for contribution in contributions),
            contributions=tuple(contributions),
        )
        for record_id, contributions in contributions_by_id.items()
    ]
    results.sort(key=lambda result: (-result.score, result.record_id))
    return results if top_k is None else results[:top_k]


__all__ = [
    "DEFAULT_RRF_RANK_CONSTANT",
    "RRFContribution",
    "RRFResult",
    "RankedList",
    "reciprocal_rank_fusion",
]
