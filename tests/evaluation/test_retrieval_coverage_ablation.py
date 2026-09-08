from __future__ import annotations

import copy
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.run_retrieval_coverage_ablation import (
    DEFAULT_RULES,
    build_links,
    canonical_digest,
    digest,
    evaluate,
    measure,
    read_archive,
    select_coverage,
)


def evidence_fixture() -> dict[str, bytes]:
    chunks = [
        {
            "chunk_id": key,
            "document_id": "D",
            "document_version": "1",
            "article_title": "其他",
            "content": key,
            "document_status": "effective",
            "effective_date": "2026-01-01",
            "expiry_date": None,
        }
        for key in "abcdef"
    ]
    judgments = [{"chunk_id": "a", "relevance": 3}, {"chunk_id": "f", "relevance": 2}]
    case = {"case_id": "SYNTHETIC", "query": "synthetic", "judgments": judgments}
    dataset = (json.dumps(case) + "\n").encode()
    score = measure(list("abcde"), judgments)
    channels = ["vector", "bm25", "hybrid", "reranked"]
    model = {"repo_id": "TEST_ONLY", "revision": "synthetic"}
    models = {"models": {"embedding": model, "reranker": model}}
    report = {
        "evaluation_mode": "bge",
        "embedding_provider": "TEST_ONLY@synthetic",
        "reranker_provider": "TEST_ONLY@synthetic",
        "total_cases": 1,
        "dataset_sha256": digest(dataset),
        "case_results": [
            {
                **case,
                "channels": [
                    {
                        "channel": channel,
                        "retrieved_chunk_ids": list("abcde"),
                        "error": None,
                        "recall_at_k": {"5": score["recall_at_5"]},
                        "reciprocal_rank": score["mrr_at_5"],
                        "ndcg_at_k": {"5": score["ndcg_at_5"]},
                    }
                    for channel in channels
                ],
            }
        ],
        "summaries": [
            {
                "channel": channel,
                "recall_at_k": {"5": score["recall_at_5"]},
                "mrr_at_k": score["mrr_at_5"],
            }
            for channel in channels
        ],
    }
    corpus = {
        "chunks": chunks,
        "allowed_chunk_ids": list("abcdef"),
        "as_of_date": "2026-09-01",
        "access_context": {},
        "full_chunk_manifest_sha256": canonical_digest(chunks),
    }
    evidence = {
        "mode": "bge",
        "status": "completed",
        "real_model_inference": True,
        "source_sha256": canonical_digest({}),
        "protocol_sha256": canonical_digest({}),
        "model_lock": models,
        "corpus_sha256": canonical_digest(chunks),
        "configuration": {
            "candidate_k_per_channel": 20,
            "rerank_window": 20,
            "rrf_rank_constant": 60,
            "final_k": 5,
        },
        "git": {},
        "cohorts": [
            {
                "name": "synthetic",
                "raw_dataset_sha256": digest(dataset),
                "case_count": 1,
                "label_status": "unit test fixture; no inference",
                "analysis": {"candidates": {"synthetic": {"hybrid": list("abcdef")}}},
            }
        ],
    }
    objects = {
        "evidence.json": evidence,
        "corpus.json": corpus,
        "source-manifest.json": {},
        "protocol.json": {},
        "models.lock.json": models,
        "synthetic/retrieval-evaluation-report.json": report,
    }
    return {
        **{key: json.dumps(value).encode() for key, value in objects.items()},
        "synthetic/dataset.jsonl": dataset,
    }


class CoverageAblationTests(unittest.TestCase):
    def setUp(self):
        self.rules = json.loads(DEFAULT_RULES.read_text(encoding="utf-8"))
        self.links = [{"rule_id": "test", "source": "a", "target": "f"}]

    def test_fifth_slot_replacement_preserves_primary_order(self):
        selected, events = select_coverage(list("abcde"), list("abcdef"), set("abcdef"), self.links)
        self.assertEqual(selected, list("abcdf"))
        self.assertEqual(events[0]["evicted"], ["e"])

    def test_missing_candidate_is_never_fetched_or_appended(self):
        selected, events = select_coverage(list("abcde"), list("abcde"), set("abcdef"), self.links)
        self.assertEqual(selected, list("abcde"))
        self.assertEqual(events[0]["action"], "outside_authorized_candidate_window")

    def test_unauthorized_candidate_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unauthorized"):
            select_coverage(list("abcde"), list("abcdef"), set("abcde"), self.links)

    def test_duplicate_and_out_of_pool_baseline_fail_closed(self):
        for baseline in (list("aacde"), list("abcdf")):
            with self.assertRaises(ValueError):
                select_coverage(baseline, list("abcde"), set("abcdef"), self.links)

    def test_existing_supplement_and_non_primary_anchor_do_not_change_ranking(self):
        for links in (
            [{"rule_id": "test", "source": "a", "target": "e"}],
            [{"rule_id": "test", "source": "c", "target": "f"}],
        ):
            self.assertEqual(
                select_coverage(list("abcde"), list("abcdef"), set("abcdef"), links)[0],
                list("abcde"),
            )

    def test_only_one_supplement_even_with_multiple_links(self):
        links = self.links + [{"rule_id": "second", "source": "b", "target": "g"}]
        self.assertEqual(
            select_coverage(list("abcde"), list("abcdefg"), set("abcdefg"), links)[0], list("abcdf")
        )

    def test_replacing_relevant_fifth_item_is_a_measured_regression(self):
        labels = [{"chunk_id": "e", "relevance": 3}]
        selected, _ = select_coverage(list("abcde"), list("abcdef"), set("abcdef"), self.links)
        self.assertEqual(measure(list("abcde"), labels)["mrr_at_5"], 0.2)
        self.assertEqual(measure(selected, labels)["recall_at_5"], 0.0)

    def test_recall_is_not_hit_rate_and_unlabeled_queries_are_rejected(self):
        labels = [{"chunk_id": "a", "relevance": 1}, {"chunk_id": "f", "relevance": 3}]
        self.assertEqual(measure(list("abcde"), labels)["recall_at_5"], 0.5)
        with self.assertRaises(ValueError):
            measure([], [])

    def test_links_require_content_same_document_and_version(self):
        source = {
            "chunk_id": "source",
            "document_id": "D",
            "document_version": "1",
            "article_title": "普通员工住宿标准",
            "content": "城市类别 住宿费用上限",
        }
        target = {
            **source,
            "chunk_id": "target",
            "article_title": "城市分类",
            "content": "城市消费水平 一类城市 二类城市",
        }
        self.assertEqual(len(build_links([source, target], self.rules)), 1)
        for field, value in (
            ("document_id", "OTHER"),
            ("document_version", "2"),
            ("content", "无关内容"),
        ):
            self.assertEqual(build_links([source, {**target, field: value}], self.rules), [])
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            build_links([source, target, {**target, "chunk_id": "duplicate_target"}], self.rules)

    def test_archive_accepts_powershell_names_and_rejects_aliases_and_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("cohort\\dataset.jsonl", "data")
            self.assertEqual(read_archive(path), {"cohort/dataset.jsonl": b"data"})
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr("cohort/dataset.jsonl", "other")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                read_archive(path)
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../secret", "data")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                read_archive(path)

    def test_end_to_end_synthetic_report_recomputes_baseline(self):
        report = evaluate(evidence_fixture(), self.rules)
        self.assertFalse(report["fresh_model_inference"])
        self.assertFalse(report["numeric_resume_ready"])
        self.assertEqual(report["cohorts"][0]["summaries"]["coverage"]["recall_at_5"], 0.5)

    def test_expiry_date_is_inclusive_like_existing_access_control(self):
        for expiry, valid in (("2026-09-01", True), ("2026-08-31", False)):
            files = evidence_fixture()
            corpus = json.loads(files["corpus.json"])
            corpus["chunks"][0]["expiry_date"] = expiry
            corpus["full_chunk_manifest_sha256"] = canonical_digest(corpus["chunks"])
            evidence = json.loads(files["evidence.json"])
            evidence["corpus_sha256"] = corpus["full_chunk_manifest_sha256"]
            files["corpus.json"] = json.dumps(corpus).encode()
            files["evidence.json"] = json.dumps(evidence).encode()
            if valid:
                self.assertEqual(evaluate(files, self.rules)["status"], "completed")
            else:
                with self.assertRaisesRegex(ValueError, "Inactive"):
                    evaluate(files, self.rules)

    def test_tampered_inputs_and_channel_errors_block_report(self):
        original = evidence_fixture()
        mutations = [
            ("corpus.json", lambda x: x["chunks"][0].update(content="modified")),
            ("source-manifest.json", lambda x: x.update(changed=True)),
            (
                "synthetic/retrieval-evaluation-report.json",
                lambda x: x["case_results"][0]["channels"][0].update(error="inference failed"),
            ),
            (
                "synthetic/retrieval-evaluation-report.json",
                lambda x: x["case_results"][0]["channels"][0].update(reciprocal_rank=0.0),
            ),
            ("evidence.json", lambda x: x.update(mode="offline")),
        ]
        for filename, mutate in mutations:
            with self.subTest(filename=filename):
                files = copy.deepcopy(original)
                value = json.loads(files[filename])
                mutate(value)
                files[filename] = json.dumps(value).encode()
                with self.assertRaises(ValueError):
                    evaluate(files, self.rules)


if __name__ == "__main__":
    unittest.main()
