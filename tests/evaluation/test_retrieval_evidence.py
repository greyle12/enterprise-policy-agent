from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.evaluation.retrieval_evidence import (
    CandidateTracingRetriever,
    MODEL_IDS,
    _model_files,
    analyze_report,
    corpus_evidence,
    file_sha256,
    load_cohorts,
    load_locked_models,
    write_json,
)
from app.evaluation.retrieval_models import RetrievalEvaluationMode
from app.evaluation.retrieval_runner import RetrievalEvaluationRunner
from app.evaluation.retrieval_runtime import build_retrieval_evaluation_runtime
from scripts import run_retrieval_evidence as cli

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_cohorts_preserve_all_legacy_cases_and_separate_generated_labels():
    protocol, cohorts = load_cohorts(ROOT, cli.PROTOCOL)
    assert [len(dataset.cases) for _, dataset in cohorts] == [20, 6]
    assert protocol["held_out_test_set"] is False
    assert cohorts[1][0]["human_reviewed"] is False
    assert sum(len(c.judgments) for c in cohorts[0][1].cases) == 30


def _copied_inputs(tmp_path):
    protocol = json.loads(cli.PROTOCOL.read_text(encoding="utf-8"))
    for cohort in protocol["cohorts"]:
        path = tmp_path / cohort["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / cohort["path"]).read_bytes())
    protocol_path = tmp_path / "protocol.json"
    write_json(protocol_path, protocol)
    return protocol, protocol_path


def test_dataset_content_drift_fails_instead_of_changing_denominator(tmp_path):
    protocol, path = _copied_inputs(tmp_path)
    data = tmp_path / protocol["cohorts"][0]["path"]
    data.write_text(data.read_text(encoding="utf-8").replace("北京", "南京"), encoding="utf-8")
    with pytest.raises(ValueError, match="dataset drift"):
        load_cohorts(tmp_path, path)


def test_windows_newlines_do_not_change_dataset_version(tmp_path):
    protocol, path = _copied_inputs(tmp_path)
    for cohort in protocol["cohorts"]:
        data = tmp_path / cohort["path"]
        data.write_bytes(b"\xef\xbb\xbf" + data.read_bytes().replace(b"\n", b"\r\n"))
    assert len(load_cohorts(tmp_path, path)[1][0][1].cases) == 20


def test_empty_judgments_are_rejected_not_counted_as_recall_one(tmp_path):
    protocol, path = _copied_inputs(tmp_path)
    data = tmp_path / protocol["cohorts"][0]["path"]
    rows = data.read_text(encoding="utf-8").splitlines()
    row = json.loads(rows[0])
    row["judgments"] = []
    rows[0] = json.dumps(row)
    data.write_text("\n".join(rows), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid retrieval case"):
        load_cohorts(tmp_path, path)


def test_capture_preserves_existing_rankings_and_records_missing_evidence():
    _, cohorts = load_cohorts(ROOT, cli.PROTOCOL)
    cases = cohorts[0][1].cases
    runtime = build_retrieval_evaluation_runtime(
        policy_directory=ROOT / "data/policies",
        cases=cases,
        mode=RetrievalEvaluationMode.OFFLINE,
    )
    captured = CandidateTracingRetriever(runtime.retriever, candidate_k=20)

    def run(target):
        return RetrievalEvaluationRunner(
            retriever=target,
            evaluation_mode=RetrievalEvaluationMode.OFFLINE,
            embedding_provider=runtime.embedding_provider,
            reranker_provider=runtime.reranker_provider,
            external_model_calls=False,
            dataset_sha256=cohorts[0][1].sha256,
            corpus_sha256=runtime.corpus_sha256,
        ).run(cases)

    plain, traced = run(runtime.retriever), run(captured)
    for left, right in zip(plain.case_results, traced.case_results, strict=True):
        for a, b in zip(left.channels, right.channels, strict=True):
            assert a.retrieved_chunk_ids == b.retrieved_chunk_ids
            assert a.recall_at_k == b.recall_at_k
            assert a.reciprocal_rank == b.reciprocal_rank
    analysis = analyze_report(traced, captured.candidates)
    expected_failures = sum(
        bool(r.error) or r.recall_at_k[5] < 1 or r.reciprocal_rank < 1
        for c in traced.case_results
        for r in c.channels
    )
    assert len(analysis["failures"]) == expected_failures
    assert all(row["effective_n"] == 20 for row in analysis["comparisons"])
    # A stronger baseline must produce a negative delta, not a fabricated uplift.
    high_baseline = traced.summaries[0].model_copy(update={"recall_at_k": {1: 1.0, 3: 1.0, 5: 1.0}})
    negative = analyze_report(
        traced.model_copy(update={"summaries": (high_baseline, *traced.summaries[1:])}),
        captured.candidates,
    )
    assert negative["comparisons"][1]["recall_delta_pp_vs_vector"] == pytest.approx(-10.0)
    assert all(len(ids) <= 20 for pool in captured.candidates.values() for ids in pool.values())
    # Existing implementation measures multi-label Recall, not Hit@5.
    assert any(
        r.recall_at_k[5] == 0.5 and r.reciprocal_rank > 0
        for c in traced.case_results
        for r in c.channels
    )
    corpus = corpus_evidence(runtime.chunks)
    assert corpus["chunk_count"] == corpus["allowed_chunk_count"] == 199
    changed = list(runtime.chunks)
    changed[0] = changed[0].model_copy(update={"allowed_departments": ["另一部门"]})
    assert (
        corpus_evidence(changed)["full_chunk_manifest_sha256"]
        != corpus["full_chunk_manifest_sha256"]
    )


def _fake_snapshot(tmp_path, monkeypatch):
    snapshot = tmp_path / "cache"
    snapshot.mkdir()
    (snapshot / "config.json").write_text("{}")
    (snapshot / "model.safetensors").write_bytes(b"unit-test-only-not-a-model")
    lock = {
        "schema_version": "1.0",
        "models": {
            role: {
                "repo_id": name,
                "revision": "a" * 40,
                "files": {
                    n: file_sha256(snapshot / n) for n in ("config.json", "model.safetensors")
                },
            }
            for role, name in MODEL_IDS.items()
        },
    }
    path = tmp_path / "lock.json"
    write_json(path, lock)
    calls = []

    def download(*args, **kwargs):
        calls.append(kwargs)
        return str(snapshot)

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    return path, snapshot, calls


def test_model_lock_uses_commit_and_offline_cache_and_verifies_weights(tmp_path, monkeypatch):
    path, snapshot, calls = _fake_snapshot(tmp_path, monkeypatch)
    load_locked_models(path)
    assert all(c["local_files_only"] and c["revision"] == "a" * 40 for c in calls)
    (snapshot / "model.safetensors").write_bytes(b"changed")
    with pytest.raises(ValueError, match="model file drift"):
        load_locked_models(path)


def test_model_lock_rejects_mutable_revision(tmp_path, monkeypatch):
    path, _, calls = _fake_snapshot(tmp_path, monkeypatch)
    lock = json.loads(path.read_text())
    lock["models"]["embedding"]["revision"] = "main"
    write_json(path, lock)
    with pytest.raises(ValueError, match="commit SHA"):
        load_locked_models(path)
    assert calls == []


def test_download_selects_one_weight_format_without_onnx_duplicates():
    files = _model_files(
        [
            "config.json",
            "model.safetensors",
            "pytorch_model.bin",
            "tokenizer.json",
            "onnx/model.onnx",
            "onnx/model.safetensors",
            "1_Pooling/config.json",
        ]
    )
    assert files == ["1_Pooling/config.json", "config.json", "model.safetensors", "tokenizer.json"]


def test_cli_offline_evidence_is_not_numeric_resume_evidence(tmp_path):
    output = tmp_path / "run"
    assert cli.main(["--mode", "offline", "--warmups", "0", "--output-dir", str(output)]) == 0
    report = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
    assert report["real_model_inference"] is False
    assert report["numeric_resume_ready"] is False
    assert [c["metric_effective_n"]["recall_at_5"] for c in report["cohorts"]] == [20, 6]
    original = (output / "evidence.json").read_bytes()
    assert cli.main(["--mode", "offline", "--output-dir", str(output)]) == 2
    assert (output / "evidence.json").read_bytes() == original
    assert not (output / "failure.json").exists()


def test_missing_model_dependency_produces_failure_evidence_and_no_scores(tmp_path, monkeypatch):
    def unavailable(**kwargs):
        raise ModuleNotFoundError("torch is unavailable")

    monkeypatch.setattr(cli, "environment_evidence", unavailable)
    output = tmp_path / "blocked"
    assert cli.main(["--output-dir", str(output)]) == 2
    failure = json.loads((output / "failure.json").read_text())
    assert failure["numeric_resume_ready"] is False
    assert not (output / "evidence.json").exists()
