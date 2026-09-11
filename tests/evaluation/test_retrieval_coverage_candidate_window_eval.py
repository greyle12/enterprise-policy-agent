from __future__ import annotations

from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts import run_retrieval_coverage_candidate_window_eval as cli
from scripts.run_retrieval_coverage_ablation import measure


def _channel(channel: str, ids: list[str], judgments: list[dict], *, error: str | None = None):
    metrics = measure(ids, judgments)
    return {
        "channel": channel,
        "retrieved_chunk_ids": ids,
        "recall_at_k": {
            "1": metrics["recall_at_5"],
            "3": metrics["recall_at_5"],
            "5": metrics["recall_at_5"],
        },
        "ndcg_at_k": {
            "1": metrics["ndcg_at_5"],
            "3": metrics["ndcg_at_5"],
            "5": metrics["ndcg_at_5"],
        },
        "first_relevant_rank": 1 if ids and ids[0] in {j["chunk_id"] for j in judgments} else None,
        "reciprocal_rank": metrics["mrr_at_5"],
        "duration_ms": 1.0,
        "error": error,
    }


def _summary(channel: str, ids: list[str], judgments: list[dict], *, error_count: int = 0):
    metrics = measure(ids, judgments)
    return {
        "channel": channel,
        "case_count": 1,
        "recall_at_k": {
            "1": metrics["recall_at_5"],
            "3": metrics["recall_at_5"],
            "5": metrics["recall_at_5"],
        },
        "mrr_at_k": metrics["mrr_at_5"],
        "ndcg_at_k": {
            "1": metrics["ndcg_at_5"],
            "3": metrics["ndcg_at_5"],
            "5": metrics["ndcg_at_5"],
        },
        "average_duration_ms": 1.0,
        "error_count": error_count,
    }


class CandidateWindowEvaluationTests(unittest.TestCase):
    def _fixture(self):
        judgments = [
            {"chunk_id": "target", "relevance": 3, "rationale": "direct answer"},
        ]
        vector = [f"v-{i:02d}" for i in range(1, 21)]
        bm25 = [f"b-{i:02d}" for i in range(1, 20)] + ["target"]
        rrf = cli.rrf_full_ranking(vector, bm25)
        case = {
            "case_id": "COV-001",
            "query": "candidate diagnostic query",
            "judgments": judgments,
        }
        report = {
            "candidate_k": 20,
            "case_results": [
                {
                    "case_id": "COV-001",
                    "query": case["query"],
                    "judgments": judgments,
                    "channels": [
                        _channel("vector", vector[:5], judgments),
                        _channel("bm25", bm25[:5], judgments),
                        _channel("hybrid", rrf[:5], judgments),
                        _channel("reranked", rrf[:5], judgments),
                    ],
                }
            ],
        }
        traces = {
            case["query"]: {
                "vector": vector,
                "bm25": bm25,
                "hybrid": rrf[:20],
            }
        }
        allowed = set(vector) | set(bm25)
        return case, report, traces, allowed, rrf

    def test_windows_are_exactly_20_and_40(self) -> None:
        self.assertEqual(cli.normalize_candidate_windows([40, 20]), (20, 40))
        for values in ([20], [20, 20], [10, 40], [20, 40, 60]):
            with self.assertRaises(ValueError):
                cli.normalize_candidate_windows(values)

    def test_rrf_reconstructs_union_before_window_cut(self) -> None:
        ranking = cli.rrf_full_ranking(["a", "b"], ["b", "c"])
        self.assertEqual(ranking, ["b", "a", "c"])
        self.assertEqual(
            len(cli.rrf_full_ranking([f"a{i}" for i in range(20)], [f"b{i}" for i in range(20)])),
            40,
        )

    def test_diagnostics_keep_before_and_after_ranks(self) -> None:
        case, report, traces, allowed, rrf = self._fixture()
        full_traces = {
            case["query"]: {
                "vector": traces[case["query"]]["vector"] + ["target"],
                "bm25": traces[case["query"]]["bm25"],
            }
        }
        rows = cli.build_diagnostic_rows(
            report=report,
            cases=(case,),
            candidates=traces,
            full_candidates=full_traces,
            allowed=allowed,
            window=20,
        )
        row = rows[0]
        target = row["relevant_ranks"][0]
        self.assertEqual(target["chunk_id"], "target")
        self.assertEqual(target["ranks"]["vector"]["before_window_truncation"], 21)
        self.assertIsNone(target["ranks"]["vector"]["after_window_truncation"])
        self.assertIsNone(target["ranks"]["vector"]["after_final_k"])
        self.assertEqual(target["ranks"]["bm25"]["before_window_truncation"], 20)
        self.assertEqual(
            target["ranks"]["rrf"]["before_window_truncation"], rrf.index("target") + 1
        )
        self.assertIsNone(target["ranks"]["rrf"]["after_window_truncation"])
        self.assertIsNone(target["ranks"]["rrf"]["after_final_k"])
        self.assertEqual(row["candidate_recall"]["rrf_before_window_truncation"], 1.0)
        self.assertEqual(row["candidate_recall"]["rrf_at_window"], 0.0)
        self.assertEqual(row["channels"]["rrf"]["top5_ids"], rrf[:5])

    def test_failed_channel_is_retained_in_n(self) -> None:
        case, report, traces, allowed, _ = self._fixture()
        report["case_results"][0]["channels"][1] = _channel(
            "bm25", [], case["judgments"], error="BM25UnsearchableQueryError: no terms"
        )
        traces[case["query"]].pop("bm25")
        vector_ids = traces[case["query"]]["vector"]
        rrf_without_bm25 = cli.rrf_full_ranking(vector_ids, [])
        traces[case["query"]]["hybrid"] = rrf_without_bm25[:20]
        report["case_results"][0]["channels"][2] = _channel(
            "hybrid", rrf_without_bm25[:5], case["judgments"]
        )
        report["case_results"][0]["channels"][3] = _channel(
            "reranked", rrf_without_bm25[:5], case["judgments"]
        )
        rows = cli.build_diagnostic_rows(
            report=report,
            cases=(case,),
            candidates=traces,
            allowed=allowed,
            window=20,
        )
        self.assertEqual(
            rows[0]["channels"]["bm25"]["error"], "BM25UnsearchableQueryError: no terms"
        )
        self.assertIsNone(rows[0]["candidate_recall"]["bm25_at_window"])

    def test_window_summary_reports_coverage_and_rerank_regression(self) -> None:
        case, report, traces, allowed, _ = self._fixture()
        rows = cli.build_diagnostic_rows(
            report=report,
            cases=(case,),
            candidates=traces,
            allowed=allowed,
            window=20,
        )
        summary = cli.build_window_summary(
            report={
                "summaries": [
                    _summary(
                        "vector",
                        report["case_results"][0]["channels"][0]["retrieved_chunk_ids"],
                        case["judgments"],
                    ),
                    _summary(
                        "bm25",
                        report["case_results"][0]["channels"][1]["retrieved_chunk_ids"],
                        case["judgments"],
                    ),
                    _summary(
                        "hybrid",
                        report["case_results"][0]["channels"][2]["retrieved_chunk_ids"],
                        case["judgments"],
                    ),
                    _summary(
                        "reranked",
                        report["case_results"][0]["channels"][3]["retrieved_chunk_ids"],
                        case["judgments"],
                    ),
                ]
            },
            rows=rows,
            window=20,
        )
        self.assertEqual(
            summary["candidate_coverage"]["rrf_before_window_truncation"]["mean_recall"], 1.0
        )
        self.assertEqual(summary["candidate_coverage"]["rrf_at_window"]["mean_recall"], 0.0)
        self.assertEqual(summary["rerank_regressions"]["effective_n"], 1)

    def test_compare_reports_both_windows_and_all_channels(self) -> None:
        def summary(window: int) -> dict:
            channels = {}
            for channel in cli.DIAGNOSTIC_CHANNELS:
                channels[channel] = {
                    "source_channel": "hybrid" if channel == "rrf" else channel,
                    "case_count": 1,
                    "effective_n": 1,
                    "successful_n": 1,
                    "recall_at_5": 0.5 if window == 20 else 1.0,
                    "mrr_at_5": 0.5 if window == 20 else 1.0,
                    "ndcg_at_5": 0.5 if window == 20 else 1.0,
                    "average_duration_ms": float(window),
                    "error_count": 0,
                }
            return {"channels": channels, "rerank_regressions": {"effective_n": 1}}

        row = {
            "case_id": "COV-001",
            "query": "q",
            "channels": {
                channel: {
                    "recall_at_5": 0.5 if channel != "reranked" else 0.0,
                    "mrr_at_5": 0.5,
                    "ndcg_at_5": 0.5,
                    "duration_ms": 20.0,
                    "top5_ids": ["a"],
                    "error": None,
                }
                for channel in cli.DIAGNOSTIC_CHANNELS
            },
            "rerank_regression_vs_rrf": {"any_regression": True},
        }
        row40 = json.loads(json.dumps(row))
        for channel in cli.DIAGNOSTIC_CHANNELS:
            row40["channels"][channel]["top5_ids"] = ["b"]
            row40["channels"][channel]["recall_at_5"] = 1.0
        comparison = cli.compare_window_reports(
            summaries={20: summary(20), 40: summary(40)},
            rows_by_window={20: [row], 40: [row40]},
        )
        self.assertEqual(len(comparison["channels"]), 4)
        vector = next(item for item in comparison["channels"] if item["channel"] == "vector")
        self.assertEqual(vector["delta_40_minus_20"]["recall_at_5"]["percentage_points"], 50.0)
        self.assertTrue(
            comparison["case_changes"][0]["channels"]["vector"]["final_ranking_changed"]
        )

    def test_main_never_overwrites_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing"
            output.mkdir()
            sentinel = output / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = cli.main(["--mode", "offline", "--output-dir", str(output)])
            self.assertEqual(code, 2)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertFalse((output / "failure.json").exists())


if __name__ == "__main__":
    unittest.main()
