import math
from collections.abc import Sequence

import pytest

from app.rag.reranking import (
    BGERerankingProvider,
    RerankCandidate,
    RerankingProvider,
    build_reranked_candidates,
    rerank_candidates,
)


class FakeArray:
    def __init__(self, values: object) -> None:
        self._values = values

    def tolist(self) -> object:
        return self._values


class FakeCrossEncoder:
    def __init__(self, outputs: object = None) -> None:
        self.outputs = [0.2, 0.9] if outputs is None else outputs
        self.calls: list[dict[str, object]] = []

    def predict(
        self,
        sentences: list[tuple[str, str]],
        **kwargs: object,
    ) -> FakeArray:
        self.calls.append({"sentences": sentences, **kwargs})
        return FakeArray(self.outputs)


class FakeRerankingProvider(RerankingProvider):
    def __init__(self, scores: Sequence[float]) -> None:
        self.scores = list(scores)
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        self.calls.append((query, list(documents)))
        return self.scores


def _candidates() -> list[RerankCandidate]:
    return [
        RerankCandidate(candidate_id="first", text="第一条制度", retrieval_score=0.9),
        RerankCandidate(candidate_id="second", text="第二条制度", retrieval_score=0.8),
    ]


def test_reranking_provider_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        RerankingProvider()


def test_bge_reranker_scores_all_pairs_in_one_call() -> None:
    model = FakeCrossEncoder()
    provider = BGERerankingProvider(model=model, batch_size=8)

    scores = provider.score("  差旅要求  ", ["第一条制度", "第二条制度"])

    assert scores == [0.2, 0.9]
    assert provider.batch_size == 8
    assert provider.model_name == "BAAI/bge-reranker-v2-m3"
    assert provider.device is None
    assert model.calls == [
        {
            "sentences": [("差旅要求", "第一条制度"), ("差旅要求", "第二条制度")],
            "batch_size": 8,
            "show_progress_bar": False,
            "convert_to_numpy": True,
        }
    ]


def test_bge_reranker_skips_model_for_empty_documents() -> None:
    model = FakeCrossEncoder(outputs=[])
    provider = BGERerankingProvider(model=model)

    assert provider.score("差旅要求", []) == []
    assert model.calls == []


@pytest.mark.parametrize(
    ("query", "documents", "message"),
    [
        (" ", ["制度"], "query"),
        ("问题", ["制度", "  "], r"documents\[1\]"),
    ],
)
def test_bge_reranker_rejects_blank_inputs(
    query: str,
    documents: list[str],
    message: str,
) -> None:
    provider = BGERerankingProvider(model=FakeCrossEncoder())

    with pytest.raises(ValueError, match=message):
        provider.score(query, documents)


@pytest.mark.parametrize("batch_size", [0, -1])
def test_bge_reranker_rejects_invalid_batch_size(batch_size: int) -> None:
    with pytest.raises(ValueError, match="batch_size"):
        BGERerankingProvider(model=FakeCrossEncoder(), batch_size=batch_size)


@pytest.mark.parametrize(
    ("outputs", "message"),
    [
        ([0.1], "score count"),
        ([0.1, "bad"], "numeric"),
        ([0.1, math.inf], "finite"),
        (0.1, "must be a list"),
    ],
)
def test_bge_reranker_rejects_invalid_model_output(outputs: object, message: str) -> None:
    provider = BGERerankingProvider(model=FakeCrossEncoder(outputs=outputs))

    with pytest.raises(RuntimeError, match=message):
        provider.score("问题", ["第一条", "第二条"])


@pytest.mark.parametrize(
    "candidate",
    [
        {"candidate_id": " ", "text": "制度", "retrieval_score": 0.5},
        {"candidate_id": "id", "text": " ", "retrieval_score": 0.5},
        {"candidate_id": "id", "text": "制度", "retrieval_score": math.nan},
    ],
)
def test_candidate_rejects_invalid_values(candidate: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RerankCandidate(**candidate)  # type: ignore[arg-type]


def test_build_ranking_is_stable_and_respects_top_k() -> None:
    ranked = build_reranked_candidates(_candidates(), [0.7, 0.7], top_k=1)

    assert [result.candidate.candidate_id for result in ranked] == ["first"]
    assert ranked[0].original_rank == 0
    assert ranked[0].rerank_score == 0.7


def test_build_ranking_rejects_duplicate_ids_and_mismatched_scores() -> None:
    first = _candidates()[0]
    with pytest.raises(ValueError, match="unique"):
        build_reranked_candidates([first, first], [0.5, 0.4])
    with pytest.raises(RuntimeError, match="score count"):
        build_reranked_candidates(_candidates(), [0.5])


def test_rerank_candidates_batches_provider_call_and_sorts_scores() -> None:
    provider = FakeRerankingProvider([0.2, 0.9])

    ranked = rerank_candidates("差旅要求", _candidates(), provider=provider)

    assert [result.candidate.candidate_id for result in ranked] == ["second", "first"]
    assert provider.calls == [("差旅要求", ["第一条制度", "第二条制度"])]


def test_rerank_empty_candidates_does_not_call_provider() -> None:
    provider = FakeRerankingProvider([])

    assert rerank_candidates("问题", [], provider=provider) == []
    assert provider.calls == []


def test_rerank_rejects_invalid_top_k() -> None:
    with pytest.raises(ValueError, match="top_k"):
        rerank_candidates("问题", [], provider=FakeRerankingProvider([]), top_k=0)
