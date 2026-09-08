"""Reproducible four-way retrieval evidence using the existing scoring framework."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

from app.evaluation.retrieval_evidence import (
    CandidateTracingRetriever,
    analyze_report,
    corpus_evidence,
    environment_evidence,
    file_sha256,
    git_identity,
    json_sha256,
    load_cohorts,
    load_locked_models,
    prepare_model_lock,
    source_manifest,
    write_evidence_markdown,
    write_json,
)
from app.evaluation.retrieval_models import RetrievalEvaluationMode
from app.evaluation.retrieval_reporting import write_retrieval_report
from app.evaluation.retrieval_runner import RetrievalEvaluationRunner
from app.evaluation.retrieval_runtime import build_retrieval_evaluation_runtime
from app.rag.embeddings import BGE_QUERY_INSTRUCTION
from app.rag.policy_chunker import chunk_policy_directory

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "tests/evaluation/retrieval_evidence_protocol.json"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("bge", "offline"), default="bge")
    parser.add_argument(
        "--prepare-models", action="store_true", help="Explicit download + model lock"
    )
    parser.add_argument(
        "--model-lock", type=Path, default=ROOT / "artifacts/retrieval-models.lock.json"
    )
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--warmups", type=int, default=1, help="Full unmeasured passes per cohort")
    parser.add_argument("--output-dir", type=Path, help="Must not already exist")
    return parser.parse_args(argv)


def run(args, output: Path) -> int:
    if args.warmups < 0 or args.threads < 1:
        raise ValueError("warmups must be nonnegative and threads must be positive")
    protocol, cohorts = load_cohorts(ROOT, PROTOCOL)
    if (protocol["candidate_k"], protocol["final_k"], protocol["rrf_rank_constant"]) != (20, 5, 60):
        raise ValueError("this version of the evidence protocol requires candidate=20, K=5, RRF=60")
    before = source_manifest(ROOT)
    identity = git_identity(ROOT)
    real = args.mode == "bge"
    environment = environment_evidence(real_models=real, device=args.device, threads=args.threads)
    lock, snapshots = load_locked_models(args.model_lock) if real else (None, {})
    mode = RetrievalEvaluationMode(args.mode)
    all_cases = tuple(case for _, dataset in cohorts for case in dataset.cases)
    runtime = build_retrieval_evaluation_runtime(
        policy_directory=ROOT / "data/policies",
        cases=all_cases,
        mode=mode,
        device=args.device,
        candidate_k=20,
        embedding_batch_size=32,
        reranker_batch_size=8,
        **(
            {"embedding_model": snapshots["embedding"], "reranker_model": snapshots["reranker"]}
            if real
            else {}
        ),
    )
    corpus = corpus_evidence(runtime.chunks)
    write_json(output / "corpus.json", corpus)
    write_json(output / "protocol.json", protocol)
    write_json(output / "source-manifest.json", before)
    write_json(output / "environment.json", environment)
    (output / "requirements-observed.txt").write_text(
        "\n".join(f"{name}=={version}" for name, version in environment["packages"].items()) + "\n",
        encoding="utf-8",
    )
    if lock:
        write_json(output / "models.lock.json", lock)
    providers = (
        {role: f"{m['repo_id']}@{m['revision']}" for role, m in lock["models"].items()}
        if lock
        else {"embedding": runtime.embedding_provider, "reranker": runtime.reranker_provider}
    )
    evidence = {
        "schema_version": "1.0",
        "mode": args.mode,
        "status": "running",
        "generated_at": datetime.now(UTC).isoformat(),
        "git": identity,
        "source_sha256": json_sha256(before),
        "protocol_sha256": json_sha256(protocol),
        "corpus_sha256": corpus["full_chunk_manifest_sha256"],
        "real_model_inference": real,
        "model_lock": lock,
        "query_instruction": BGE_QUERY_INSTRUCTION,
        "configuration": {
            "vector_index": "InMemoryVectorIndex exact cosine; normalized BGE in bge mode",
            "bm25": "InMemoryBM25Index; NFKC/CJK bigrams; k1=1.2; b=0.75; positive scores only",
            "text": "PolicyChunk.retrieval_text for every channel",
            "candidate_k_per_channel": 20,
            "rrf_rank_constant": 60,
            "rrf_union_upper_bound": 40,
            "rerank_window": 20,
            "final_k": 5,
            "embedding_batch_size": 32,
            "reranker_batch_size": 8,
            "warmup_full_passes_per_cohort": args.warmups,
            "measured_repetitions": 1,
            "latency_for_resume": False,
            "latency_scope": "query embedding + retrieval (+ fusion/reranker); no loading/indexing",
            "empty_or_unlabeled_queries": "reject before inference; zero samples in these cohorts",
            "unauthorized_judgments": "reject before inference; test ACL separately",
            "missing_judgments": "unjudged results count as no hit; relevance completeness unknown",
            "query_errors": "retain in N with zero scores, mark experiment incomplete",
        },
        "cohorts": [],
    }
    chunks_by_id = {chunk.chunk_id: chunk for chunk in runtime.chunks}
    for cohort, dataset in cohorts:
        print(
            f"Evaluating {cohort['name']}: {len(dataset.cases)} queries, mode={args.mode}",
            flush=True,
        )
        target = CandidateTracingRetriever(runtime.retriever, candidate_k=20)
        runner = RetrievalEvaluationRunner(
            retriever=target,
            evaluation_mode=mode,
            embedding_provider=providers["embedding"],
            reranker_provider=providers["reranker"],
            external_model_calls=real,
            dataset_sha256=dataset.sha256,
            corpus_sha256=runtime.corpus_sha256,
            candidate_k=20,
        )
        for _ in range(args.warmups):
            warmup = runner.run(dataset.cases)
            if any(s.error_count for s in warmup.summaries):
                write_retrieval_report(warmup, output / cohort["name"] / "failed-warmup")
                raise RuntimeError("warmup failed; see failed-warmup report")
        target.candidates.clear()
        report = runner.run(dataset.cases)
        directory = output / cohort["name"]
        write_retrieval_report(report, directory)
        shutil.copyfile(dataset.path, directory / "dataset.jsonl")
        review = [
            {
                "case_id": case.case_id,
                "query": case.query,
                "labels": [
                    {**j.model_dump(mode="json"), "text": chunks_by_id[j.chunk_id].retrieval_text}
                    for j in case.judgments
                ],
            }
            for case in dataset.cases
        ]
        write_json(directory / "judgment-review.json", review)
        evidence["cohorts"].append(
            {
                **cohort,
                "raw_dataset_sha256": dataset.sha256,
                "judgment_count": sum(len(case.judgments) for case in dataset.cases),
                "metric_effective_n": {
                    "recall_at_5": len(dataset.cases),
                    "mrr_at_5": len(dataset.cases),
                },
                "no_answer_n": 0,
                "unauthorized_n": 0,
                "unlabeled_n": 0,
                "analysis": analyze_report(report, target.candidates),
            }
        )
        if file_sha256(dataset.path) != dataset.sha256:
            raise RuntimeError("dataset changed during evaluation; discard the run")
    after = corpus_evidence(chunk_policy_directory(ROOT / "data/policies"))
    if (
        before != source_manifest(ROOT)
        or after["full_chunk_manifest_sha256"] != evidence["corpus_sha256"]
        or json_sha256(json.loads(PROTOCOL.read_text(encoding="utf-8")))
        != evidence["protocol_sha256"]
    ):
        raise RuntimeError("source, protocol or corpus changed during evaluation; discard the run")
    errors = sum(
        row["error_count"] for c in evidence["cohorts"] for row in c["analysis"]["comparisons"]
    )
    evidence["status"] = "completed" if not errors else "incomplete_channel_errors"
    evidence["numeric_resume_ready"] = real and not errors
    evidence["resume_scope"] = "legacy_dev only, disclose incomplete single-developer judgments"
    write_json(output / "evidence.json", evidence)
    write_evidence_markdown(output / "evidence.md", evidence)
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "mode": args.mode,
                "numeric_resume_ready": evidence["numeric_resume_ready"],
                "report": str(output / "evidence.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    # Threshold failure is a measured outcome, not an execution failure. No tuning to force a pass.
    return 0 if not errors else 1


def main(argv=None) -> int:
    args = parse_args(argv)
    output = None
    try:
        if args.prepare_models:
            prepare_model_lock(args.model_lock)
            print(f"Models downloaded and pinned: {args.model_lock}")
            return 0
        output = args.output_dir or ROOT / "artifacts" / (
            f"retrieval-evidence-{args.mode}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
        )
        output.mkdir(parents=True, exist_ok=False)
        return run(args, output)
    except Exception as exc:  # noqa: BLE001 - CLI failure is evidence, never a fallback score
        failure = {
            "status": "blocked",
            "mode": args.mode,
            "git": git_identity(ROOT),
            "requested_device": args.device,
            "requested_models": {
                "embedding": "BAAI/bge-small-zh-v1.5",
                "reranker": "BAAI/bge-reranker-v2-m3",
            },
            "real_model_inference_completed": False,
            "numeric_resume_ready": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        # Never add files to an existing user output directory on a rejected overwrite.
        if output is not None and not isinstance(exc, FileExistsError):
            write_json(output / "failure.json", failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
