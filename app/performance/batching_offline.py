from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass

from app.performance.batching import (
    BatchExecutionObservation,
    BatchOptimizationRunner,
    BatchOptimizationScenario,
)
from app.performance.models import BatchOptimizationReport, BatchOptimizationScenarioName
from app.rag.embeddings import BGEEmbeddingProvider
from app.rag.reranking import (
    BGERerankingProvider,
    RerankCandidate,
    build_reranked_candidates,
    rerank_candidates,
)


@dataclass(frozen=True, slots=True)
class _OfflineArray:
    values: object

    def tolist(self) -> object:
        return self.values


def _normalized_vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = [float(digest[index] + 1) for index in range(4)]
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]


def _rerank_score(query: str, document: str) -> float:
    digest = hashlib.sha256(f"{query}\0{document}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / ((1 << 64) - 1)


def _output_digest(values: object) -> str:
    encoded = json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class _OfflineEmbeddingModel:
    def __init__(self, *, call_overhead_ms: float, batch_latency_ms: float) -> None:
        self._call_overhead_ms = call_overhead_ms
        self._batch_latency_ms = batch_latency_ms
        self.calls = 0
        self.internal_batches = 0

    def get_embedding_dimension(self) -> int:
        return 4

    def encode(
        self,
        sentences: str | list[str],
        *,
        batch_size: int,
        show_progress_bar: bool,
        convert_to_numpy: bool,
        normalize_embeddings: bool,
    ) -> _OfflineArray:
        del show_progress_bar, convert_to_numpy, normalize_embeddings
        texts = [sentences] if isinstance(sentences, str) else sentences
        batches = math.ceil(len(texts) / batch_size)
        self.calls += 1
        self.internal_batches += batches
        time.sleep((self._call_overhead_ms + batches * self._batch_latency_ms) / 1_000)
        vectors = [_normalized_vector(text) for text in texts]
        return _OfflineArray(vectors[0] if isinstance(sentences, str) else vectors)


class _OfflineCrossEncoderModel:
    def __init__(self, *, call_overhead_ms: float, batch_latency_ms: float) -> None:
        self._call_overhead_ms = call_overhead_ms
        self._batch_latency_ms = batch_latency_ms
        self.calls = 0
        self.internal_batches = 0

    def predict(
        self,
        sentences: list[tuple[str, str]],
        *,
        batch_size: int,
        show_progress_bar: bool,
        convert_to_numpy: bool,
    ) -> _OfflineArray:
        del show_progress_bar, convert_to_numpy
        batches = math.ceil(len(sentences) / batch_size)
        self.calls += 1
        self.internal_batches += batches
        time.sleep((self._call_overhead_ms + batches * self._batch_latency_ms) / 1_000)
        return _OfflineArray([_rerank_score(query, document) for query, document in sentences])


def _embedding_scenario(
    *,
    texts: Sequence[str],
    batch_size: int,
    call_overhead_ms: float,
    batch_latency_ms: float,
) -> BatchOptimizationScenario:
    text_list = list(texts)

    def execute(*, batched: bool) -> BatchExecutionObservation:
        model = _OfflineEmbeddingModel(
            call_overhead_ms=call_overhead_ms,
            batch_latency_ms=batch_latency_ms,
        )
        provider = BGEEmbeddingProvider(model=model, batch_size=batch_size)
        if batched:
            vectors = provider.embed_documents(text_list)
        else:
            vectors = [vector for text in text_list for vector in provider.embed_documents([text])]
        return BatchExecutionObservation(
            output_digest=_output_digest(vectors),
            output_order=tuple(_output_digest(vector) for vector in vectors),
            provider_calls=model.calls,
            internal_batches=model.internal_batches,
        )

    return BatchOptimizationScenario(
        name=BatchOptimizationScenarioName.EMBEDDING_DOCUMENTS,
        description="比较制度文档逐条向量化与一次列表向量化。",
        item_count=len(text_list),
        batch_size=batch_size,
        expected_sequential_provider_calls=len(text_list),
        expected_batched_provider_calls=1,
        expected_sequential_internal_batches=len(text_list),
        expected_batched_internal_batches=math.ceil(len(text_list) / batch_size),
        run_sequential=lambda: execute(batched=False),
        run_batched=lambda: execute(batched=True),
    )


def _reranker_scenario(
    *,
    candidates: Sequence[RerankCandidate],
    batch_size: int,
    call_overhead_ms: float,
    batch_latency_ms: float,
) -> BatchOptimizationScenario:
    query = "差旅住宿发票报销要求"
    candidate_list = list(candidates)

    def execute(*, batched: bool) -> BatchExecutionObservation:
        model = _OfflineCrossEncoderModel(
            call_overhead_ms=call_overhead_ms,
            batch_latency_ms=batch_latency_ms,
        )
        provider = BGERerankingProvider(model=model, batch_size=batch_size)
        if batched:
            ranked = rerank_candidates(query, candidate_list, provider=provider)
            scores_by_id = {result.candidate.candidate_id: result.rerank_score for result in ranked}
            scores = [scores_by_id[candidate.candidate_id] for candidate in candidate_list]
        else:
            scores = [provider.score(query, [candidate.text])[0] for candidate in candidate_list]
            ranked = build_reranked_candidates(candidate_list, scores)

        return BatchExecutionObservation(
            output_digest=_output_digest(scores),
            output_order=tuple(result.candidate.candidate_id for result in ranked),
            provider_calls=model.calls,
            internal_batches=model.internal_batches,
        )

    return BatchOptimizationScenario(
        name=BatchOptimizationScenarioName.RERANKER_CANDIDATES,
        description="比较候选制度逐条打分与 query-document pairs 批量打分。",
        item_count=len(candidate_list),
        batch_size=batch_size,
        expected_sequential_provider_calls=len(candidate_list),
        expected_batched_provider_calls=1,
        expected_sequential_internal_batches=len(candidate_list),
        expected_batched_internal_batches=math.ceil(len(candidate_list) / batch_size),
        run_sequential=lambda: execute(batched=False),
        run_batched=lambda: execute(batched=True),
    )


def run_offline_batch_optimization(
    *,
    item_count: int = 32,
    batch_size: int = 8,
    call_overhead_ms: float = 1.5,
    batch_latency_ms: float = 0.25,
) -> BatchOptimizationReport:
    """Compare sequential and batch-first Embedding/Reranker calls offline."""

    if item_count < 1:
        raise ValueError("item_count must be greater than zero")
    if batch_size < 1:
        raise ValueError("batch_size must be greater than zero")
    if call_overhead_ms <= 0:
        raise ValueError("call_overhead_ms must be greater than zero")
    if batch_latency_ms <= 0:
        raise ValueError("batch_latency_ms must be greater than zero")

    texts = [f"制度条款 {index:03d}：差旅与采购办理要求。" for index in range(item_count)]
    candidates = [
        RerankCandidate(
            candidate_id=f"policy-{index:03d}",
            text=f"候选制度 {index:03d}：住宿发票、采购审批与报销材料。",
            retrieval_score=1.0 - index / (item_count + 1),
        )
        for index in range(item_count)
    ]
    runner = BatchOptimizationRunner(
        scenarios=(
            _embedding_scenario(
                texts=texts,
                batch_size=batch_size,
                call_overhead_ms=call_overhead_ms,
                batch_latency_ms=batch_latency_ms,
            ),
            _reranker_scenario(
                candidates=candidates,
                batch_size=batch_size,
                call_overhead_ms=call_overhead_ms,
                batch_latency_ms=batch_latency_ms,
            ),
        ),
        simulated_call_overhead_ms=call_overhead_ms,
        simulated_batch_latency_ms=batch_latency_ms,
    )
    return runner.run()
