"""Run fresh BGE retrieval for the confirmed, frozen targeted challenge set."""

import argparse
import importlib.util
import json
from pathlib import Path
from time import perf_counter

from scripts.experiment_coverage_gate import select_gated
from scripts.coverage_gate_v2 import select_v2
from scripts.replay_coverage_windows import select_window
from scripts.run_retrieval_coverage_ablation import build_links, digest, measure, require
from scripts.run_retrieval_coverage_confirmed_eval import (
    DEFAULT_MODEL_LOCK,
    DEFAULT_REVIEW_DIR,
    ROOT,
    load_confirmed_inputs,
)

CHALLENGE = ROOT / "artifacts/gate-v2-unseen-v1"


def load_challenge():
    lock = json.loads((CHALLENGE / "draft-lock.json").read_text(encoding="utf-8"))
    freeze = json.loads((CHALLENGE / "freeze.json").read_text(encoding="utf-8"))
    require(
        digest((CHALLENGE / "freeze.json").read_bytes()) == lock["freeze_sha256"],
        "freeze changed",
    )
    for name, sha in freeze["files"].items():
        require(digest((ROOT / name).read_bytes()) == sha, f"frozen file changed: {name}")
    raw = (CHALLENGE / "review-draft.json").read_bytes()
    require(digest(raw) == lock["draft_sha256"], "draft changed")
    confirmation = json.loads((CHALLENGE / "user-confirmation.json").read_text(encoding="utf-8"))
    require(
        confirmation["status"] == "user_confirmed" and confirmation["draft_sha256"] == digest(raw),
        "confirmation mismatch",
    )
    return json.loads(raw), confirmation


def score_selection(case, base, pool, allowed, links, window):
    start = perf_counter()
    frozen, events = select_window(base, pool, allowed, links, window=window)
    v1, reason = select_gated(case["query"], base, frozen, events, "explicit_veto")
    v2, clauses = select_v2(case["query"], base, frozen, events)
    elapsed = (perf_counter() - start) * 1000
    judgments = [{"chunk_id": j["chunk_id"], "relevance": j["grade"]} for j in case["judgments"]]
    ids = {"raw": base, "frozen": frozen, "v1": v1, "v2": v2}
    metrics = {k: measure(v, judgments) for k, v in ids.items()}
    return {
        "case_id": case["case_id"],
        "query": case["query"],
        "supplement_needed": case["supplement_needed"],
        "judgments": judgments,
        "ids": ids,
        "metrics": metrics,
        "events": events,
        "gate_reason": reason,
        "opportunity": any(e["action"] == "supplemented" for e in events),
        "blocked": frozen != v2,
        "v1_blocked": frozen != v1,
        "clauses": clauses,
        "evicted_ids": [i for i in base if i not in v2],
        "regressions": [
            k for k in metrics["v2"] if metrics["v2"][k] < metrics["frozen"][k] - 1e-12
        ],
        "selection_ms": elapsed,
    }


def run(args):
    draft, confirmation = load_challenge()
    inputs = load_confirmed_inputs(DEFAULT_REVIEW_DIR)
    if importlib.util.find_spec("torch") is None:
        raise RuntimeError("torch is not installed; no real inference executed")
    from app.evaluation.retrieval_evidence import (
        CandidateTracingRetriever,
        environment_evidence,
        load_locked_models,
        git_identity,
        source_manifest,
    )
    from app.evaluation.retrieval_models import RetrievalCase, RetrievalEvaluationMode
    from app.evaluation.retrieval_runtime import build_retrieval_evaluation_runtime
    from app.evaluation.retrieval_runner import RetrievalEvaluationRunner

    env = environment_evidence(real_models=True, device=args.device, threads=4)
    lock, snapshots = load_locked_models(args.model_lock)
    require(lock == inputs.freeze["model_lock"], "model lock drift")
    cases = [
        RetrievalCase(
            case_id=f"RET-{i:03}",
            title=c["case_id"],
            query=c["query"],
            judgments=[
                {"chunk_id": j["chunk_id"], "relevance": j["grade"], "rationale": c["rationale"]}
                for j in c["judgments"]
            ],
        )
        for i, c in enumerate(draft["cases"], 1)
    ]
    require(
        all(
            j["chunk_id"] in inputs.allowed_chunk_ids
            for c in draft["cases"]
            for j in c["judgments"]
        ),
        "unauthorized judgment",
    )
    links = build_links(inputs.corpus["chunks"], inputs.rules)
    result = {
        "status": "running",
        "new_model_inference": True,
        "numeric_resume_ready": False,
        "independent_blind_test": False,
        "suite": "v2_frozen_unseen_targeted",
        "draft": draft,
        "gate_freeze": json.loads((CHALLENGE / "freeze.json").read_text(encoding="utf-8")),
        "runner_sha256": digest(Path(__file__).read_bytes()),
        "confirmation": confirmation,
        "models": lock,
        "environment": env,
        "git": git_identity(ROOT),
        "source": source_manifest(ROOT),
        "configuration": {"windows": [20, 40], "final_k": 5, "warmups": 0, "rrf_constant": 60},
        "timing_scope": "frozen selection plus v1 and v2 selection; excludes model inference and scoring; single sample",
        "windows": {},
    }
    for window in (20, 40):
        runtime = build_retrieval_evaluation_runtime(
            policy_directory=ROOT / "data/policies",
            cases=tuple(cases),
            mode=RetrievalEvaluationMode.BGE,
            device=args.device,
            candidate_k=window,
            embedding_batch_size=32,
            reranker_batch_size=8,
            embedding_model=snapshots["embedding"],
            reranker_model=snapshots["reranker"],
        )
        require(runtime.corpus_sha256 == inputs.runtime_corpus_sha256, "corpus drift")
        traced = CandidateTracingRetriever(runtime.retriever, candidate_k=window)
        runner = RetrievalEvaluationRunner(
            retriever=traced,
            evaluation_mode=RetrievalEvaluationMode.BGE,
            embedding_provider=snapshots["embedding"],
            reranker_provider=snapshots["reranker"],
            external_model_calls=True,
            dataset_sha256=digest((CHALLENGE / "review-draft.json").read_bytes()),
            corpus_sha256=runtime.corpus_sha256,
            candidate_k=window,
        )
        print(f"BGE window={window}, N={len(cases)}", flush=True)
        report = runner.run(tuple(cases)).model_dump(mode="json")
        write(args.output_dir / f"raw-{window}.json", report)
        write(args.output_dir / f"candidates-{window}.json", traced.candidates)
        require(
            all(ch["error"] is None for c in report["case_results"] for ch in c["channels"]),
            "channel errors; raw report retained, no acceptance result",
        )
        rows = []
        for case, captured in zip(draft["cases"], report["case_results"], strict=True):
            require(case["query"] == captured["query"], "query mismatch")
            base = next(
                ch["retrieved_chunk_ids"]
                for ch in captured["channels"]
                if ch["channel"] == "reranked"
            )
            rows.append(
                score_selection(
                    case,
                    base,
                    traced.candidates[case["query"]]["hybrid"],
                    set(inputs.allowed_chunk_ids),
                    links,
                    window,
                )
            )
        groups = {}
        for group, subset in [
            ("all", rows),
            ("controls", [r for r in rows if not r["supplement_needed"]]),
            ("needs_supplement", [r for r in rows if r["supplement_needed"]]),
        ]:
            groups[group] = {
                "n": len(subset),
                "metrics": {
                    s: {
                        k: sum(r["metrics"][s][k] for r in subset) / len(subset)
                        for k in rows[0]["metrics"][s]
                    }
                    for s in ("raw", "frozen", "v1", "v2")
                },
                "opportunities": sum(r["opportunity"] for r in subset),
                "blocked": sum(r["blocked"] for r in subset),
                "v1_blocked": sum(r["v1_blocked"] for r in subset),
            }
        result["windows"][str(window)] = {"groups": groups, "cases": rows}
    load_challenge()
    require(source_manifest(ROOT) == result["source"], "source changed during evaluation")
    result["status"] = "completed"
    write(args.output_dir / "v2-unseen-report.json", result)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--model-lock", type=Path, default=DEFAULT_MODEL_LOCK)
    p.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = p.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    try:
        run(args)
    except Exception as exc:
        write(
            args.output_dir / "failure.json",
            {"status": "blocked", "error": str(exc), "numeric_resume_ready": False},
        )
        raise


if __name__ == "__main__":
    main()
