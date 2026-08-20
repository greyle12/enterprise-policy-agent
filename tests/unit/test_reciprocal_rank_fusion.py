from __future__ import annotations

import pytest

from app.rag.fusion import RankedList, reciprocal_rank_fusion


def test_rrf_fuses_rankings_deduplicates_and_preserves_contributions() -> None:
    results = reciprocal_rank_fusion(
        [
            RankedList(source="vector", record_ids=("a", "b", "c")),
            RankedList(source="bm25", record_ids=("b", "d", "a")),
        ]
    )

    assert [result.record_id for result in results] == ["b", "a", "d", "c"]
    assert results[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert [(item.source, item.rank) for item in results[0].contributions] == [
        ("vector", 2),
        ("bm25", 1),
    ]


def test_rrf_uses_rank_not_incompatible_raw_retrieval_scores() -> None:
    results = reciprocal_rank_fusion(
        [
            RankedList(source="vector", record_ids=("shared", "vector-only")),
            RankedList(source="bm25", record_ids=("shared", "bm25-only")),
        ],
        top_k=1,
    )

    assert [result.record_id for result in results] == ["shared"]
    assert results[0].score == pytest.approx(2 / 61)


def test_rrf_accepts_an_empty_channel_and_breaks_ties_by_record_id() -> None:
    results = reciprocal_rank_fusion(
        [
            RankedList(source="vector", record_ids=("b", "a")),
            RankedList(source="bm25", record_ids=()),
        ]
    )

    assert [result.record_id for result in results] == ["b", "a"]

    tied = reciprocal_rank_fusion(
        [
            RankedList(source="vector", record_ids=("z",)),
            RankedList(source="bm25", record_ids=("a",)),
        ]
    )
    assert [result.record_id for result in tied] == ["a", "z"]


@pytest.mark.parametrize("rank_constant", [0, -1, 1.5, True])
def test_rrf_rejects_invalid_rank_constant(rank_constant: object) -> None:
    with pytest.raises(ValueError, match="rank_constant"):
        reciprocal_rank_fusion(
            [
                RankedList(source="vector", record_ids=("a",)),
                RankedList(source="bm25", record_ids=("a",)),
            ],
            rank_constant=rank_constant,  # type: ignore[arg-type]
        )


def test_rrf_rejects_invalid_rankings_and_limit() -> None:
    with pytest.raises(ValueError, match="at least two"):
        reciprocal_rank_fusion([RankedList(source="vector", record_ids=("a",))])
    with pytest.raises(ValueError, match="sources"):
        reciprocal_rank_fusion(
            [
                RankedList(source="same", record_ids=("a",)),
                RankedList(source="same", record_ids=("b",)),
            ]
        )
    with pytest.raises(ValueError, match="unique"):
        RankedList(source="vector", record_ids=("a", "a"))
    with pytest.raises(ValueError, match="top_k"):
        reciprocal_rank_fusion(
            [
                RankedList(source="vector", record_ids=("a",)),
                RankedList(source="bm25", record_ids=("a",)),
            ],
            top_k=0,
        )
