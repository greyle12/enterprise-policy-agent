"""Diagnose retrieval candidate rankings and compare frozen windows 20 and 40.

This is a retrieval-only, targeted development-set experiment.  It reuses the
user-confirmed COV labels and frozen corpus/ACL snapshot, and changes exactly
one variable: the candidate window.  Vector, BM25, RRF (the existing
``hybrid`` channel), and BGE reranking all use the same window inside each
group and return a fixed final Top-5.  No selector, production provider,
SQLite data, or LangGraph checkpoint is changed.

``bge`` mode is the only mode that can produce real semantic measurements.
``offline`` is a deterministic wiring smoke test and is always marked
``numeric_resume_ready=false``.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import math
from pathlib import Path
from time import perf_counter
import sys
from typing import Any, Mapping, Sequence
from uuid import uuid4

from scripts.run_retrieval_coverage_ablation import (
    canonical_digest,
    digest,
    measure,
)
from scripts.run_retrieval_coverage_confirmed_eval import (
    DEFAULT_MODEL_LOCK,
    DEFAULT_REVIEW_DIR,
    ROOT,
    SELECTOR,
    TARGET_LABEL_STATUS,
    build_target_dataset_rows,
    load_confirmed_inputs,
    remap_report_case_ids,
    target_dataset_bytes,
)


WINDOWS = (20, 40)
FINAL_K = 5
RRF_RANK_CONSTANT = 60
CHANNELS = ("vector", "bm25", "hybrid", "reranked")
DIAGNOSTIC_CHANNELS = ("vector", "bm25", "rrf", "reranked")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_candidate_windows(values: Sequence[int]) -> tuple[int, int]:
    """Require the scope-specific 20/40 comparison, in stable order."""

    normalized = tuple(values)
    _require(
        len(normalized) == 2 and all(type(value) is int for value in normalized),
        "candidate windows must contain exactly two integers",
    )
    _require(len(set(normalized)) == 2, "candidate windows must be unique")
    _require(set(normalized) == set(WINDOWS), "candidate windows must be exactly 20 and 40")
    _require(all(value >= FINAL_K for value in normalized), "candidate windows must be >= final K")
    return WINDOWS


def rank_of(ids: Sequence[str], chunk_id: str) -> int | None:
    """Return one-based rank, or None when a chunk was truncated/absent."""

    try:
        return list(ids).index(chunk_id) + 1
    except ValueError:
        return None


def rrf_full_ranking(
    vector_ids: Sequence[str],
    bm25_ids: Sequence[str],
    *,
    rank_constant: int = RRF_RANK_CONSTANT,
) -> list[str]:
    """Reproduce the existing RRF ordering before its candidate-window cut.

    ``PolicyRetriever.search_hybrid`` asks the fusion function for ``top_k``
    results and therefore returns only the first W of the union.  The two
    source lists captured by ``CandidateTracingRetriever`` are enough to
    recover the complete at-most-2W union without changing production code.
    """

    _require(type(rank_constant) is int and rank_constant >= 1, "invalid RRF rank constant")
    scores: dict[str, float] = {}
    for ids in (vector_ids, bm25_ids):
        normalized = list(ids)
        _require(len(set(normalized)) == len(normalized), "duplicate source candidate ID")
        for rank, chunk_id in enumerate(normalized, start=1):
            _require(isinstance(chunk_id, str) and chunk_id, "invalid source candidate ID")
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (rank_constant + rank)
    return sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))


def _at5(values: Mapping[Any, Any]) -> float:
    value = values.get("5", values.get(5))
    _require(value is not None, "report is missing K=5 metric")
    return float(value)


def _channel_result(case_result: Mapping[str, Any], channel: str) -> dict[str, Any]:
    for result in case_result.get("channels", ()):
        if result.get("channel") == channel:
            return dict(result)
    raise ValueError(f"report is missing channel: {channel}")


def _ids(value: Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    return list(value)


def _validate_ids(ids: Sequence[str], allowed: set[str], *, limit: int, label: str) -> list[str]:
    normalized = list(ids)
    _require(len(normalized) <= limit, f"{label} exceeds candidate window")
    _require(len(set(normalized)) == len(normalized), f"{label} contains duplicate IDs")
    _require(set(normalized) <= allowed, f"{label} contains unauthorized IDs")
    return normalized


def _channel_view(
    result: Mapping[str, Any],
    *,
    candidate_ids: Sequence[str],
    final_ids: Sequence[str],
) -> dict[str, Any]:
    return {
        "candidate_ids": list(candidate_ids),
        "top5_ids": list(final_ids),
        "duration_ms": float(result.get("duration_ms", 0.0)),
        "recall_at_5": _at5(result.get("recall_at_k", {})),
        "mrr_at_5": float(result.get("reciprocal_rank", 0.0)),
        "ndcg_at_5": _at5(result.get("ndcg_at_k", {})),
        "first_relevant_rank": result.get("first_relevant_rank"),
        "error": result.get("error"),
    }


def _rank_bundle(
    judgment: Mapping[str, Any],
    *,
    vector_full_ids: Sequence[str],
    vector_window_ids: Sequence[str],
    vector_top5: Sequence[str],
    bm25_full_ids: Sequence[str],
    bm25_window_ids: Sequence[str],
    bm25_top5: Sequence[str],
    rrf_full_ids: Sequence[str],
    rrf_window_ids: Sequence[str],
    rrf_top5: Sequence[str],
    reranked_top5: Sequence[str],
) -> dict[str, Any]:
    chunk_id = str(judgment["chunk_id"])
    return {
        "chunk_id": chunk_id,
        "relevance": int(judgment["relevance"]),
        "rationale": judgment["rationale"],
        "ranks": {
            "vector": {
                "before_window_truncation": rank_of(vector_full_ids, chunk_id),
                "after_window_truncation": rank_of(vector_window_ids, chunk_id),
                "after_final_k": rank_of(vector_top5, chunk_id),
            },
            "bm25": {
                "before_window_truncation": rank_of(bm25_full_ids, chunk_id),
                "after_window_truncation": rank_of(bm25_window_ids, chunk_id),
                "after_final_k": rank_of(bm25_top5, chunk_id),
            },
            "rrf": {
                "before_window_truncation": rank_of(rrf_full_ids, chunk_id),
                "after_window_truncation": rank_of(rrf_window_ids, chunk_id),
                "after_final_k": rank_of(rrf_top5, chunk_id),
            },
            "reranked": {
                "input_rrf_rank": rank_of(rrf_window_ids, chunk_id),
                "after_final_k": rank_of(reranked_top5, chunk_id),
            },
        },
    }


def _regression(
    hybrid: Mapping[str, Any],
    reranked: Mapping[str, Any],
) -> dict[str, Any]:
    recall_delta = float(reranked["recall_at_5"]) - float(hybrid["recall_at_5"])
    mrr_delta = float(reranked["mrr_at_5"]) - float(hybrid["mrr_at_5"])
    ndcg_delta = float(reranked["ndcg_at_5"]) - float(hybrid["ndcg_at_5"])
    return {
        "recall_at_5_delta": recall_delta,
        "mrr_at_5_delta": mrr_delta,
        "ndcg_at_5_delta": ndcg_delta,
        "recall_regressed": recall_delta < -1e-12,
        "mrr_regressed": mrr_delta < -1e-12,
        "ndcg_regressed": ndcg_delta < -1e-12,
        "any_regression": any(delta < -1e-12 for delta in (recall_delta, mrr_delta, ndcg_delta)),
    }


def capture_full_source_traces(
    retriever: Any,
    queries: Sequence[str],
    *,
    full_k: int,
) -> tuple[dict[str, dict[str, list[str]]], dict[str, dict[str, str | None]], dict[str, float]]:
    """Capture the authorized full Vector/BM25 rankings outside measured latency.

    The normal evaluator deliberately asks each source for W items.  For a
    candidate-stage diagnosis we also need to know whether a judged chunk was
    ranked at 21+ (or absent).  This helper uses the already restricted
    retriever with ``top_k=allowed_chunk_count`` after the measured pass; its
    timing is reported separately and never mixed into channel latency.
    """

    _require(type(full_k) is int and full_k >= FINAL_K, "full source top_k must be >= final K")
    traces: dict[str, dict[str, list[str]]] = {}
    errors: dict[str, dict[str, str | None]] = {}
    durations: dict[str, float] = {}
    for query in queries:
        started = perf_counter()
        query_traces: dict[str, list[str]] = {}
        query_errors: dict[str, str | None] = {"vector": None, "bm25": None}
        try:
            query_traces["vector"] = [
                result.chunk.chunk_id for result in retriever.search(query, top_k=full_k)
            ]
        except Exception as exc:  # noqa: BLE001 - preserve diagnostic failure per source
            query_traces["vector"] = []
            query_errors["vector"] = f"{type(exc).__name__}: {exc}"
        try:
            query_traces["bm25"] = [
                result.chunk.chunk_id for result in retriever.search_keywords(query, top_k=full_k)
            ]
        except Exception as exc:  # noqa: BLE001 - preserve diagnostic failure per source
            query_traces["bm25"] = []
            query_errors["bm25"] = f"{type(exc).__name__}: {exc}"
        traces[query] = query_traces
        errors[query] = query_errors
        durations[query] = max(0.0, (perf_counter() - started) * 1000.0)
    return traces, errors, durations


def build_diagnostic_rows(
    *,
    report: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    candidates: Mapping[str, Mapping[str, Sequence[str]]],
    full_candidates: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
    full_candidate_errors: Mapping[str, Mapping[str, str | None]] | None = None,
    full_candidate_durations_ms: Mapping[str, float] | None = None,
    allowed: set[str],
    window: int,
) -> list[dict[str, Any]]:
    """Join frozen judgments to every pre/post truncation ranking.

    Rows remain present when a channel failed.  In that case the runner's
    zero-valued metrics and the channel error are retained, while unavailable
    ranks are ``null``.  This prevents an error from disappearing from N.
    """

    _require(int(report.get("candidate_k", -1)) == window, "report/window mismatch")
    captured_cases = list(report.get("case_results", ()))
    _require(len(captured_cases) == len(cases), "case count mismatch in diagnostic report")
    rows: list[dict[str, Any]] = []
    for case, captured in zip(cases, captured_cases, strict=True):
        query = str(case["query"])
        by_channel = {channel: _channel_result(captured, channel) for channel in CHANNELS}
        trace = candidates.get(query, {})
        vector_ids = _ids(trace.get("vector"))
        bm25_ids = _ids(trace.get("bm25"))
        rrf_window_ids = _ids(trace.get("hybrid"))
        vector_result = by_channel["vector"]
        bm25_result = by_channel["bm25"]
        hybrid_result = by_channel["hybrid"]
        reranked_result = by_channel["reranked"]
        vector_top5 = _ids(vector_result.get("retrieved_chunk_ids"))
        bm25_top5 = _ids(bm25_result.get("retrieved_chunk_ids"))
        rrf_top5 = _ids(hybrid_result.get("retrieved_chunk_ids"))
        reranked_top5 = _ids(reranked_result.get("retrieved_chunk_ids"))
        relevant = {str(judgment["chunk_id"]) for judgment in case["judgments"]}
        full_trace = (full_candidates or {}).get(query, {})
        if full_candidates is None:
            vector_full_ids = vector_ids
            bm25_full_ids = bm25_ids
        else:
            vector_full_ids = _ids(full_trace.get("vector"))
            bm25_full_ids = _ids(full_trace.get("bm25"))
        trace_errors = (full_candidate_errors or {}).get(query, {})

        # Successful traces are checked against the authorization snapshot and
        # the public report.  A failed channel may have no trace and remains
        # auditable instead of being silently omitted.
        if vector_result.get("error") is None:
            _validate_ids(vector_ids, allowed, limit=window, label=f"vector/{case['case_id']}")
            _require(set(vector_top5) <= set(vector_ids), "vector Top-5 escaped candidate list")
            if trace_errors.get("vector") is None:
                _validate_ids(
                    vector_full_ids,
                    allowed,
                    limit=max(window, len(vector_full_ids)),
                    label=f"vector-full/{case['case_id']}",
                )
                _require(
                    set(vector_ids) <= set(vector_full_ids),
                    "vector candidate window escaped full ranking",
                )
        if bm25_result.get("error") is None:
            _validate_ids(bm25_ids, allowed, limit=window, label=f"bm25/{case['case_id']}")
            _require(set(bm25_top5) <= set(bm25_ids), "BM25 Top-5 escaped candidate list")
            if trace_errors.get("bm25") is None:
                _validate_ids(
                    bm25_full_ids,
                    allowed,
                    limit=max(window, len(bm25_full_ids)),
                    label=f"bm25-full/{case['case_id']}",
                )
                _require(
                    set(bm25_ids) <= set(bm25_full_ids),
                    "BM25 candidate window escaped full ranking",
                )
        if hybrid_result.get("error") is None or reranked_result.get("error") is None:
            _validate_ids(rrf_window_ids, allowed, limit=window, label=f"rrf/{case['case_id']}")
            _require(set(rrf_top5) <= set(rrf_window_ids), "RRF Top-5 escaped candidate list")
            _require(set(reranked_top5) <= set(rrf_window_ids), "reranker escaped RRF candidates")

        # Production order: source lists are cut to W, RRF is computed over
        # their <=2W union, then that union is cut to W.  Keep the hypothetical
        # full-source RRF only as an extra diagnostic; it is not a production
        # score because its source ranks were never passed to PolicyRetriever.
        rrf_full_ids = rrf_full_ranking(vector_full_ids, bm25_full_ids)
        rrf_pretruncate_ids = rrf_full_ranking(vector_ids, bm25_ids)
        if rrf_window_ids and not trace_errors.get("vector") and not trace_errors.get("bm25"):
            _require(
                rrf_window_ids == rrf_pretruncate_ids[:window],
                f"captured RRF ranking mismatch for {case['case_id']}",
            )
        channel_views = {
            "vector": _channel_view(vector_result, candidate_ids=vector_ids, final_ids=vector_top5),
            "bm25": _channel_view(bm25_result, candidate_ids=bm25_ids, final_ids=bm25_top5),
            "rrf": _channel_view(hybrid_result, candidate_ids=rrf_window_ids, final_ids=rrf_top5),
            "reranked": _channel_view(
                reranked_result, candidate_ids=rrf_window_ids, final_ids=reranked_top5
            ),
        }
        # The independent metric implementation is checked for successful
        # channels, so a report cannot accidentally mix another K or formula.
        for channel, ids in (
            ("vector", vector_top5),
            ("bm25", bm25_top5),
            ("hybrid", rrf_top5),
            ("reranked", reranked_top5),
        ):
            result = by_channel[channel]
            if result.get("error") is None:
                measured = measure(ids, list(case["judgments"]))
                _require(
                    math.isclose(
                        measured["recall_at_5"], _at5(result["recall_at_k"]), abs_tol=1e-12
                    )
                    and math.isclose(
                        measured["mrr_at_5"], float(result["reciprocal_rank"]), abs_tol=1e-12
                    )
                    and math.isclose(
                        measured["ndcg_at_5"], _at5(result["ndcg_at_k"]), abs_tol=1e-12
                    ),
                    f"metric mismatch for {case['case_id']}/{channel}",
                )

        vector_available = vector_result.get("error") is None and trace_errors.get("vector") is None
        bm25_available = bm25_result.get("error") is None and trace_errors.get("bm25") is None
        rrf_available = (
            hybrid_result.get("error") is None
            and trace_errors.get("vector") is None
            and trace_errors.get("bm25") is None
        )
        candidate_recall = {
            "vector_at_window": len(relevant.intersection(vector_ids)) / len(relevant)
            if vector_available
            else None,
            "bm25_at_window": len(relevant.intersection(bm25_ids)) / len(relevant)
            if bm25_available
            else None,
            "rrf_before_window_truncation": len(relevant.intersection(rrf_pretruncate_ids))
            / len(relevant)
            if rrf_available
            else None,
            "rrf_at_window": len(relevant.intersection(rrf_window_ids)) / len(relevant)
            if rrf_available
            else None,
            "rrf_from_full_source_diagnostic": len(relevant.intersection(rrf_full_ids))
            / len(relevant)
            if vector_available and bm25_available
            else None,
        }
        rows.append(
            {
                "case_id": case["case_id"],
                "query": query,
                "judgments": [dict(judgment) for judgment in case["judgments"]],
                "relevant_count": len(relevant),
                "window": window,
                "candidate_traces": {
                    "vector_before_window_truncation": vector_full_ids,
                    "vector_after_window_truncation": vector_ids,
                    "bm25_before_window_truncation": bm25_full_ids,
                    "bm25_after_window_truncation": bm25_ids,
                    "rrf_before_window_truncation": rrf_pretruncate_ids,
                    "rrf_after_window_truncation": rrf_window_ids,
                    "rrf_from_full_source_diagnostic": rrf_full_ids,
                },
                "full_candidate_trace_errors": {
                    "vector": trace_errors.get("vector"),
                    "bm25": trace_errors.get("bm25"),
                },
                "full_candidate_trace_duration_ms": float(
                    (full_candidate_durations_ms or {}).get(query, 0.0)
                ),
                "channels": channel_views,
                "candidate_recall": candidate_recall,
                "relevant_ranks": [
                    _rank_bundle(
                        judgment,
                        vector_full_ids=vector_full_ids,
                        vector_window_ids=vector_ids,
                        vector_top5=vector_top5,
                        bm25_full_ids=bm25_full_ids,
                        bm25_window_ids=bm25_ids,
                        bm25_top5=bm25_top5,
                        rrf_full_ids=rrf_pretruncate_ids,
                        rrf_window_ids=rrf_window_ids,
                        rrf_top5=rrf_top5,
                        reranked_top5=reranked_top5,
                    )
                    for judgment in case["judgments"]
                ],
                "rerank_regression_vs_rrf": _regression(
                    channel_views["rrf"], channel_views["reranked"]
                ),
            }
        )
    return rows


def _summary_metrics(summary: Mapping[str, Any]) -> dict[str, Any]:
    error_count = int(summary.get("error_count", 0))
    case_count = int(summary.get("case_count", 0))
    return {
        "source_channel": summary["channel"],
        "case_count": case_count,
        "effective_n": case_count,
        "successful_n": max(0, case_count - error_count),
        "recall_at_5": _at5(summary.get("recall_at_k", {})),
        "mrr_at_5": float(summary.get("mrr_at_k", 0.0)),
        "ndcg_at_5": _at5(summary.get("ndcg_at_k", {})),
        "average_duration_ms": float(summary.get("average_duration_ms", 0.0)),
        "error_count": error_count,
    }


def _candidate_coverage(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    values = [
        float(row["candidate_recall"][key])
        for row in rows
        if row["candidate_recall"].get(key) is not None
        and row["channels"]["rrf" if key.startswith("rrf") else key.split("_", 1)[0]]["error"]
        is None
    ]
    return {
        "effective_n": len(values),
        "mean_recall": sum(values) / len(values) if values else None,
        "error_count": len(rows) - len(values),
    }


def rerank_regression_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    successful = [
        row
        for row in rows
        if row["channels"]["rrf"]["error"] is None and row["channels"]["reranked"]["error"] is None
    ]
    recall_regressed = [
        row["case_id"] for row in successful if row["rerank_regression_vs_rrf"]["recall_regressed"]
    ]
    mrr_regressed = [
        row["case_id"] for row in successful if row["rerank_regression_vs_rrf"]["mrr_regressed"]
    ]
    ndcg_regressed = [
        row["case_id"] for row in successful if row["rerank_regression_vs_rrf"]["ndcg_regressed"]
    ]
    any_regressed = [
        row["case_id"] for row in successful if row["rerank_regression_vs_rrf"]["any_regression"]
    ]
    return {
        "effective_n": len(successful),
        "recall_regression_count": len(recall_regressed),
        "recall_regressed_case_ids": recall_regressed,
        "mrr_regression_count": len(mrr_regressed),
        "mrr_regressed_case_ids": mrr_regressed,
        "ndcg_regression_count": len(ndcg_regressed),
        "ndcg_regressed_case_ids": ndcg_regressed,
        "any_regression_count": len(any_regressed),
        "any_regressed_case_ids": any_regressed,
    }


def build_window_summary(
    *,
    report: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    window: int,
) -> dict[str, Any]:
    summaries = {str(item["channel"]): _summary_metrics(item) for item in report["summaries"]}
    channels = {
        "vector": summaries["vector"],
        "bm25": summaries["bm25"],
        "rrf": {**summaries["hybrid"], "source_channel": "hybrid"},
        "reranked": summaries["reranked"],
    }
    return {
        "window": window,
        "candidate_k_per_channel": window,
        "rrf_rank_constant": RRF_RANK_CONSTANT,
        "rerank_window": window,
        "final_k": FINAL_K,
        "query_count": len(rows),
        "judgment_count": sum(row["relevant_count"] for row in rows),
        "channels": channels,
        "candidate_coverage": {
            "vector_at_window": _candidate_coverage(rows, "vector_at_window"),
            "bm25_at_window": _candidate_coverage(rows, "bm25_at_window"),
            "rrf_before_window_truncation": _candidate_coverage(
                rows, "rrf_before_window_truncation"
            ),
            "rrf_at_window": _candidate_coverage(rows, "rrf_at_window"),
        },
        "full_source_trace": {
            "effective_n": sum(
                not any(row["full_candidate_trace_errors"].values()) for row in rows
            ),
            "error_count": sum(any(row["full_candidate_trace_errors"].values()) for row in rows),
            "average_duration_ms": (
                sum(row["full_candidate_trace_duration_ms"] for row in rows) / len(rows)
                if rows
                else 0.0
            ),
            "scope": "diagnostic-only; excluded from channel duration_ms",
        },
        "rerank_regressions": rerank_regression_summary(rows),
    }


def _delta(left: float, right: float) -> dict[str, float]:
    difference = right - left
    return {"absolute": difference, "percentage_points": difference * 100.0}


def compare_window_reports(
    *,
    summaries: Mapping[int, Mapping[str, Any]],
    rows_by_window: Mapping[int, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Compare both windows without selecting or hiding a preferred result."""

    _require(set(summaries) == set(WINDOWS), "comparison requires both 20 and 40")
    channel_rows = []
    for channel in DIAGNOSTIC_CHANNELS:
        left = summaries[20]["channels"][channel]
        right = summaries[40]["channels"][channel]
        channel_rows.append(
            {
                "channel": channel,
                "source_channel": left["source_channel"],
                "window_20": left,
                "window_40": right,
                "delta_40_minus_20": {
                    "recall_at_5": _delta(left["recall_at_5"], right["recall_at_5"]),
                    "mrr_at_5": _delta(left["mrr_at_5"], right["mrr_at_5"]),
                    "ndcg_at_5": _delta(left["ndcg_at_5"], right["ndcg_at_5"]),
                    "average_duration_ms": right["average_duration_ms"]
                    - left["average_duration_ms"],
                    "error_count": right["error_count"] - left["error_count"],
                },
            }
        )

    left_rows = {row["case_id"]: row for row in rows_by_window[20]}
    right_rows = {row["case_id"]: row for row in rows_by_window[40]}
    _require(set(left_rows) == set(right_rows), "case IDs differ between candidate windows")
    case_changes = []
    for case_id in (row["case_id"] for row in rows_by_window[20]):
        left = left_rows[case_id]
        right = right_rows[case_id]
        channel_changes: dict[str, Any] = {}
        for channel in DIAGNOSTIC_CHANNELS:
            left_channel = left["channels"][channel]
            right_channel = right["channels"][channel]
            channel_changes[channel] = {
                "recall_at_5": _delta(left_channel["recall_at_5"], right_channel["recall_at_5"]),
                "mrr_at_5": _delta(left_channel["mrr_at_5"], right_channel["mrr_at_5"]),
                "ndcg_at_5": _delta(left_channel["ndcg_at_5"], right_channel["ndcg_at_5"]),
                "duration_ms": right_channel["duration_ms"] - left_channel["duration_ms"],
                "final_ranking_changed": left_channel["top5_ids"] != right_channel["top5_ids"],
            }
        case_changes.append(
            {
                "case_id": case_id,
                "query": left["query"],
                "channels": channel_changes,
                "rerank_regression_window_20": left["rerank_regression_vs_rrf"],
                "rerank_regression_window_40": right["rerank_regression_vs_rrf"],
            }
        )
    return {
        "baseline_window": 20,
        "comparison_window": 40,
        "channels": channel_rows,
        "case_changes": case_changes,
        "rerank_regressions": {
            "window_20": summaries[20]["rerank_regressions"],
            "window_40": summaries[40]["rerank_regressions"],
        },
    }


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.2%}"


def render_markdown(evidence: Mapping[str, Any]) -> str:
    comparison = evidence["comparison"]
    lines = [
        "# 候选阶段诊断与 20/40 窗口消融",
        "",
        f"- 状态：`{evidence['status']}`；模式：`{evidence['mode']}`；真实模型推理：`{str(evidence['real_model_inference']).lower()}`",
        f"- 查询数：{evidence['query_count']}；相关性判断数：{evidence['judgment_count']}；标签：`{evidence['label_status']}`",
        "- 这是冻结规则后的用户确认 AI 辅助开发集，不是独立人工测试集；`numeric_resume_ready` 固定为 `false`。",
        "- 唯一自变量是候选窗口 20/40；四路均使用同一窗口、RRF=60、重排同窗、最终 K=5。",
        "- 延迟为单次固定顺序诊断，包含查询向量化（BM25 不含向量化），排除模型加载和建库；不作生产 SLA 声明。",
        "",
        "## 两窗口四路结果",
        "",
        "| 通道 | 窗口 | 有效样本 N | Recall@5 | MRR@5 | nDCG@5 | 平均耗时 ms | 错误 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison["channels"]:
        for window_key in ("window_20", "window_40"):
            item = row[window_key]
            lines.append(
                f"| `{row['channel']}` | {window_key.split('_')[1]} | {item['effective_n']} | "
                f"{_pct(item['recall_at_5'])} | {_pct(item['mrr_at_5'])} | {_pct(item['ndcg_at_5'])} | "
                f"{item['average_duration_ms']:.3f} | {item['error_count']} |"
            )
    lines += [
        "",
        "## 40 − 20 差值",
        "",
        "| 通道 | Recall 差值 pp | MRR 差值 pp | nDCG 差值 pp | 耗时差值 ms | 错误差值 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in comparison["channels"]:
        delta = row["delta_40_minus_20"]
        lines.append(
            f"| `{row['channel']}` | {delta['recall_at_5']['percentage_points']:+.2f} | "
            f"{delta['mrr_at_5']['percentage_points']:+.2f} | "
            f"{delta['ndcg_at_5']['percentage_points']:+.2f} | "
            f"{delta['average_duration_ms']:+.3f} | {delta['error_count']:+d} |"
        )
    lines += ["", "## 候选覆盖与重排回归", ""]
    for window in WINDOWS:
        summary = evidence["windows"][str(window)]
        coverage = summary["candidate_coverage"]
        regression = summary["rerank_regressions"]
        lines += [
            f"### 窗口 {window}",
            "",
            "| 阶段 | 候选覆盖宏平均 | 有效样本 N |",
            "|---|---:|---:|",
        ]
        for label, key in (
            ("Vector", "vector_at_window"),
            ("BM25", "bm25_at_window"),
            ("RRF 并集（截断前）", "rrf_before_window_truncation"),
            ("RRF 窗口（截断后）", "rrf_at_window"),
        ):
            item = coverage[key]
            lines.append(f"| {label} | {_pct(item['mean_recall'])} | {item['effective_n']} |")
        lines += [
            "",
            f"重排相对 RRF：Recall 回归 {regression['recall_regression_count']} 条，"
            f"MRR 回归 {regression['mrr_regression_count']} 条，"
            f"nDCG 回归 {regression['ndcg_regression_count']} 条；"
            f"任一指标回归 {regression['any_regression_count']} 条。",
            f"全量 Vector/BM25 诊断扫描平均耗时 {summary['full_source_trace']['average_duration_ms']:.3f} ms，"
            f"诊断错误 {summary['full_source_trace']['error_count']} 条（不计入通道耗时）。",
            "",
        ]
    lines += [
        "## 诊断字段",
        "",
        "每个 `window-*/candidate-diagnostics.json` 行都保存：Vector/BM25 全量排名、窗口排名及 Top-5、"
        "RRF 未截断并集排名、RRF 窗口排名、RRF Top-5、Reranker 输入排名和最终 Top-5；"
        "相关 Chunk 还带 relevance grade 与 rationale。缺失排名为 `null`，不是人工判定无关。",
        "",
        "指标分母是每条查询的全部冻结正相关 Chunk，按查询宏平均；错误查询保留在 N 中并记零，"
        "同时由 `error_count` 与 `successful_n` 显式报告。",
        "",
        "## 局限",
        "",
        "标签集合仍是用户确认的 AI 辅助开发集，未完成全授权语料的独立双人复核；20/40 只回答候选窗口"
        "对当前语料的影响，不能外推线上 Query、生产延迟或回答准确率。窗口 40 可能因 RRF 竞争和重排误差"
        "导致最终 Top-5 回归，因此报告保留所有窗口和逐查询变化，不自动选择配置。",
        "",
    ]
    return "\n".join(lines)


def _protocol(
    inputs: Any,
    *,
    mode: str,
    lock: dict[str, Any] | None,
    dataset_sha256: str,
    warmups: int,
    embedding_batch_size: int,
    reranker_batch_size: int,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "suite_name": "enterprise_policy_agent_retrieval_candidate_window_diagnostic",
        "scope": "frozen_targeted_development_set_candidate_window_ablation",
        "label_status": TARGET_LABEL_STATUS,
        "numeric_resume_ready": False,
        "input": {
            "review_freeze_sha256": canonical_digest(inputs.freeze),
            "confirmed_review_sha256": canonical_digest(inputs.review),
            "user_confirmation_sha256": canonical_digest(inputs.confirmation),
            "query_set_sha256": canonical_digest(inputs.queries),
            "corpus_snapshot_sha256": canonical_digest(inputs.corpus),
            "rules_sha256": canonical_digest(inputs.rules),
            "selector_sha256_lf": digest(SELECTOR.read_bytes().replace(b"\r\n", b"\n")),
            "dataset_sha256": dataset_sha256,
        },
        "mode": mode,
        "models": {
            "lock_sha256": canonical_digest(lock) if lock else None,
            "real_model_inference_required": mode == "bge",
        },
        "configuration": {
            "candidate_windows": list(WINDOWS),
            "vector_candidate_k": "window",
            "bm25_candidate_k": "window",
            "rrf_candidate_k": "window",
            "rrf_rank_constant": RRF_RANK_CONSTANT,
            "rrf_union_upper_bound": "2 * window",
            "rerank_window": "window",
            "final_k": FINAL_K,
            "full_source_trace_top_k": "authorized_chunk_count",
            "full_source_trace_latency": "reported separately; excluded from measured channel latency",
            "embedding_batch_size": embedding_batch_size,
            "reranker_batch_size": reranker_batch_size,
            "warmups": warmups,
            "measured_repetitions": 1,
            "same_inputs_per_window": True,
            "coverage_rules_frozen_but_not_applied": True,
            "latency_scope": "query embedding + retrieval/fusion/reranker; no model loading/indexing",
            "latency_for_resume": False,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--model-lock", type=Path, default=DEFAULT_MODEL_LOCK)
    parser.add_argument("--mode", choices=("bge", "offline"), default="bge")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--embedding-batch-size", type=int, default=32)
    parser.add_argument("--reranker-batch-size", type=int, default=8)
    parser.add_argument("--candidate-windows", nargs="+", type=int, default=list(WINDOWS))
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args(argv)


def run(args: argparse.Namespace, output: Path) -> int:
    windows = normalize_candidate_windows(args.candidate_windows)
    _require(args.threads >= 1, "threads must be positive")
    _require(args.warmups >= 0, "warmups must be nonnegative")
    _require(args.embedding_batch_size >= 1, "embedding batch size must be positive")
    _require(args.reranker_batch_size >= 1, "reranker batch size must be positive")
    inputs = load_confirmed_inputs(args.review_dir)

    # Delayed imports keep freeze/argument/unit-test validation available on a
    # machine that does not have the application runtime or torch installed.
    from app.evaluation.retrieval_evidence import (
        CandidateTracingRetriever,
        corpus_evidence,
        environment_evidence,
        git_identity,
        load_locked_models,
        source_manifest,
        write_json,
    )
    from app.evaluation.retrieval_models import RetrievalCase, RetrievalEvaluationMode
    from app.evaluation.retrieval_reporting import write_retrieval_report
    from app.evaluation.retrieval_runner import RetrievalEvaluationRunner
    from app.evaluation.retrieval_runtime import build_retrieval_evaluation_runtime
    from app.rag.policy_chunker import chunk_policy_directory

    real = args.mode == "bge"
    before_source = source_manifest(ROOT)
    identity = git_identity(ROOT)
    environment = environment_evidence(
        real_models=real,
        device=args.device,
        threads=args.threads,
    )
    lock, snapshots = load_locked_models(args.model_lock) if real else (None, {})
    if real:
        _require(lock == inputs.freeze.get("model_lock"), "model lock differs from frozen review")

    raw_dataset = target_dataset_bytes(inputs.cases)
    dataset_sha256 = digest(raw_dataset)
    runner_cases = tuple(
        RetrievalCase.model_validate(row) for row in build_target_dataset_rows(inputs.cases)
    )
    mode = RetrievalEvaluationMode(args.mode)
    protocol = _protocol(
        inputs,
        mode=args.mode,
        lock=lock,
        dataset_sha256=dataset_sha256,
        warmups=args.warmups,
        embedding_batch_size=args.embedding_batch_size,
        reranker_batch_size=args.reranker_batch_size,
    )
    write_json(output / "source-manifest.json", before_source)
    write_json(output / "environment.json", environment)
    write_json(output / "protocol.json", protocol)
    write_json(output / "frozen-corpus.json", inputs.corpus)
    write_json(output / "queries.json", inputs.queries)
    write_json(output / "review.user-confirmed.json", inputs.review)
    write_json(output / "user-confirmation.json", inputs.confirmation)
    write_json(output / "rules.json", inputs.rules)
    (output / "target-dataset.jsonl").write_bytes(raw_dataset)
    (output / "requirements-observed.txt").write_text(
        "\n".join(f"{name}=={version}" for name, version in environment["packages"].items()) + "\n",
        encoding="utf-8",
    )
    if lock:
        write_json(output / "models.lock.json", lock)

    providers: dict[str, str] | None = None
    window_rows: dict[int, list[dict[str, Any]]] = {}
    window_summaries: dict[int, dict[str, Any]] = {}
    frozen_live_corpus: dict[str, Any] | None = None
    frozen_runtime_sha256: str | None = None

    for window in windows:
        runtime_kwargs: dict[str, Any] = {
            "policy_directory": ROOT / "data/policies",
            "cases": runner_cases,
            "mode": mode,
            "device": args.device,
            "candidate_k": window,
            "embedding_batch_size": args.embedding_batch_size,
            "reranker_batch_size": args.reranker_batch_size,
        }
        if real:
            runtime_kwargs.update(
                embedding_model=snapshots["embedding"],
                reranker_model=snapshots["reranker"],
            )
        runtime = build_retrieval_evaluation_runtime(**runtime_kwargs)
        live_corpus = corpus_evidence(runtime.chunks)
        _require(
            live_corpus["full_chunk_manifest_sha256"]
            == inputs.corpus["full_chunk_manifest_sha256"],
            "live corpus differs from frozen full manifest",
        )
        _require(
            runtime.corpus_sha256 == inputs.runtime_corpus_sha256,
            "live runtime corpus fingerprint drift",
        )
        if frozen_live_corpus is None:
            frozen_live_corpus = live_corpus
            frozen_runtime_sha256 = runtime.corpus_sha256
            write_json(output / "corpus.json", live_corpus)
            chunks_by_id = {chunk.chunk_id: chunk for chunk in runtime.chunks}
            write_json(
                output / "judgment-review.json",
                [
                    {
                        "case_id": case["case_id"],
                        "query": case["query"],
                        "labels": [
                            {
                                **judgment,
                                "text": chunks_by_id[judgment["chunk_id"]].retrieval_text,
                            }
                            for judgment in case["judgments"]
                        ],
                    }
                    for case in inputs.cases
                ],
            )
        else:
            _require(live_corpus == frozen_live_corpus, "corpus evidence differs between windows")
            _require(
                runtime.corpus_sha256 == frozen_runtime_sha256,
                "runtime corpus differs between windows",
            )

        providers = (
            {
                role: f"{model['repo_id']}@{model['revision']}"
                for role, model in lock["models"].items()
            }
            if lock
            else {
                "embedding": runtime.embedding_provider,
                "reranker": runtime.reranker_provider,
            }
        )
        target = CandidateTracingRetriever(runtime.retriever, candidate_k=window)
        runner = RetrievalEvaluationRunner(
            retriever=target,
            evaluation_mode=mode,
            embedding_provider=providers["embedding"],
            reranker_provider=providers["reranker"],
            external_model_calls=real,
            dataset_sha256=dataset_sha256,
            corpus_sha256=runtime.corpus_sha256,
            candidate_k=window,
        )
        window_dir = output / f"window-{window}"
        window_dir.mkdir(parents=True, exist_ok=True)
        for warmup_number in range(args.warmups):
            warmup = runner.run(runner_cases)
            if any(summary.error_count for summary in warmup.summaries):
                write_json(
                    window_dir / "warmup-failure.json",
                    {"warmup": warmup_number + 1, "report": warmup.model_dump(mode="json")},
                )
                raise RuntimeError(f"warmup failed for candidate window {window}")
        target.candidates.clear()
        print(
            f"Evaluating candidate window {window}: {len(runner_cases)} queries, mode={args.mode}",
            flush=True,
        )
        measured = runner.run(runner_cases)
        public_report = remap_report_case_ids(measured, inputs.cases)
        report_dict = public_report.model_dump(mode="json")
        traces = {
            query: {channel: list(ids) for channel, ids in by_channel.items()}
            for query, by_channel in target.candidates.items()
        }
        full_traces, full_trace_errors, full_trace_durations = capture_full_source_traces(
            runtime.retriever,
            [case["query"] for case in inputs.cases],
            full_k=runtime.retriever.allowed_chunk_count,
        )
        rows = build_diagnostic_rows(
            report=report_dict,
            cases=inputs.cases,
            candidates=traces,
            full_candidates=full_traces,
            full_candidate_errors=full_trace_errors,
            full_candidate_durations_ms=full_trace_durations,
            allowed=set(inputs.allowed_chunk_ids),
            window=window,
        )
        summary = build_window_summary(report=report_dict, rows=rows, window=window)
        window_rows[window] = rows
        window_summaries[window] = summary
        write_retrieval_report(public_report, window_dir)
        write_json(window_dir / "candidate-traces.json", traces)
        write_json(
            window_dir / "full-source-traces.json",
            {
                "full_k": runtime.retriever.allowed_chunk_count,
                "traces": full_traces,
                "errors": full_trace_errors,
                "duration_ms": full_trace_durations,
            },
        )
        write_json(window_dir / "candidate-diagnostics.json", rows)
        write_json(window_dir / "window-summary.json", summary)
        write_json(window_dir / "retrieval-evaluation-report.json", report_dict)
        (window_dir / "target-dataset.jsonl").write_bytes(raw_dataset)

    _require(frozen_live_corpus is not None, "no window was evaluated")
    after_source = source_manifest(ROOT)
    after_corpus = corpus_evidence(chunk_policy_directory(ROOT / "data/policies"))
    _require(before_source == after_source, "source files changed during evaluation")
    _require(
        after_corpus["full_chunk_manifest_sha256"]
        == frozen_live_corpus["full_chunk_manifest_sha256"],
        "corpus changed during evaluation",
    )
    comparison = compare_window_reports(summaries=window_summaries, rows_by_window=window_rows)
    errors = sum(
        summary["channels"][channel]["error_count"]
        for summary in window_summaries.values()
        for channel in DIAGNOSTIC_CHANNELS
    ) + sum(summary["full_source_trace"]["error_count"] for summary in window_summaries.values())
    status = "completed" if errors == 0 else "incomplete_channel_errors"
    evidence = {
        "schema_version": "1.0",
        "suite_name": "enterprise_policy_agent_retrieval_candidate_window_diagnostic",
        "status": status,
        "mode": args.mode,
        "real_model_inference": real and status == "completed",
        "numeric_resume_ready": False,
        "label_status": TARGET_LABEL_STATUS,
        "independent_test_set": False,
        "query_count": len(inputs.cases),
        "judgment_count": sum(len(case["judgments"]) for case in inputs.cases),
        "dataset_sha256": dataset_sha256,
        "corpus_sha256": frozen_runtime_sha256,
        "frozen_full_chunk_manifest_sha256": inputs.corpus["full_chunk_manifest_sha256"],
        "source_sha256": canonical_digest(before_source),
        "protocol_sha256": canonical_digest(protocol),
        "environment_sha256": canonical_digest(environment),
        "model_lock_sha256": canonical_digest(lock) if lock else None,
        "git": identity,
        "configuration": protocol["configuration"],
        "windows": {str(window): window_summaries[window] for window in windows},
        "comparison": comparison,
        "artifacts": {
            "reports": {
                str(window): f"window-{window}/retrieval-evaluation-report.json"
                for window in windows
            },
            "candidate_traces": {
                str(window): f"window-{window}/candidate-traces.json" for window in windows
            },
            "candidate_diagnostics": {
                str(window): f"window-{window}/candidate-diagnostics.json" for window in windows
            },
            "full_source_traces": {
                str(window): f"window-{window}/full-source-traces.json" for window in windows
            },
        },
        "latency_claim": None,
        "runtime_backend_changed": False,
        "sqlite_data_migrated": False,
        "langgraph_checkpoint_backend_changed": False,
    }
    write_json(output / "comparison.json", comparison)
    write_json(
        output / "candidate-diagnostics.json",
        {str(window): window_rows[window] for window in windows},
    )
    write_json(output / "candidate-window-report.json", evidence)
    (output / "candidate-window-report.md").write_text(render_markdown(evidence), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": status,
                "mode": args.mode,
                "real_model_inference": evidence["real_model_inference"],
                "numeric_resume_ready": False,
                "windows": list(windows),
                "query_count": len(inputs.cases),
                "report": str(output / "candidate-window-report.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if status == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = args.output_dir or ROOT / "artifacts" / (
        f"retrieval-candidate-window-{args.mode}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    )
    created = False
    try:
        output.mkdir(parents=True, exist_ok=False)
        created = True
        return run(args, output)
    except Exception as exc:  # noqa: BLE001 - failures are evidence, never fallback scores
        failure = {
            "schema_version": "1.0",
            "status": "blocked",
            "phase": "candidate_window_diagnostic",
            "mode": args.mode,
            "candidate_windows": getattr(args, "candidate_windows", list(WINDOWS)),
            "real_model_inference_completed": False,
            "numeric_resume_ready": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        if created:
            _write_json(output / "failure.json", failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
