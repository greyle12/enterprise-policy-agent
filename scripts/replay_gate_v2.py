"""Replay both development suites without changing any frozen input."""

import argparse
import json
import platform
from pathlib import Path
from time import perf_counter
from scripts.coverage_gate_v2 import select_v2
from scripts.experiment_coverage_gate import select_gated
from scripts.replay_coverage_windows import replay, select_window
from scripts.run_gate_challenge import load_challenge
from scripts.run_retrieval_coverage_ablation import (
    build_links,
    digest,
    measure,
    read_archive,
    require,
)
from scripts.run_retrieval_coverage_confirmed_eval import DEFAULT_REVIEW_DIR, load_confirmed_inputs


def compare(rows):
    out = []
    for r in rows:
        v1, _ = select_gated(r["query"], r["base"], r["frozen"], r["events"], "explicit_veto")
        start = perf_counter()
        v2, trace = select_v2(r["query"], r["base"], r["frozen"], r["events"])
        elapsed = (perf_counter() - start) * 1000
        ids = {"raw": r["base"], "frozen": r["frozen"], "v1": v1, "v2": v2}
        ms = {k: measure(v, r["judgments"]) for k, v in ids.items()}
        out.append(
            {
                **r,
                "ids": ids,
                "metrics": ms,
                "clauses": trace,
                "selection_ms": elapsed,
                "opportunity": r["base"] != r["frozen"],
                "v2_blocked": v2 != r["frozen"],
                "evicted_ids": [i for i in r["base"] if i not in v2],
                "regressions_vs_frozen": [
                    k for k in ms["v2"] if ms["v2"][k] < ms["frozen"][k] - 1e-12
                ],
                "regressions_vs_v1": [k for k in ms["v2"] if ms["v2"][k] < ms["v1"][k] - 1e-12],
            }
        )
    groups = {}
    for name, subset in [
        ("all", out),
        ("controls", [r for r in out if r["control"]]),
        ("needs", [r for r in out if not r["control"]]),
    ]:
        groups[name] = {
            "n": len(subset),
            "metrics": {
                s: {
                    k: sum(r["metrics"][s][k] for r in subset) / len(subset)
                    for k in out[0]["metrics"][s]
                }
                for s in ("raw", "frozen", "v1", "v2")
            },
            "opportunities": sum(r["opportunity"] for r in subset),
            "v2_blocked": sum(r["v2_blocked"] for r in subset),
        }
    return {"groups": groups, "cases": out}


def run(legacy, challenge):
    inputs = load_confirmed_inputs(DEFAULT_REVIEW_DIR)
    old = replay(legacy, DEFAULT_REVIEW_DIR)
    draft, confirmation = load_challenge()
    files = read_archive(challenge)
    report = json.loads(files["challenge-report.json"])
    require(
        report["status"] == "completed" and report["new_model_inference"] is True,
        "real completed report required",
    )
    require(
        report["draft"] == draft and report["confirmation"] == confirmation,
        "challenge labels/confirmation changed",
    )
    require(report["models"] == inputs.freeze["model_lock"], "model drift")
    links = build_links(inputs.corpus["chunks"], inputs.rules)
    result = {
        "status": "completed",
        "scope": "development_regression_only",
        "new_model_inference": False,
        "numeric_resume_ready": False,
        "input_sha256": {
            "legacy": digest(legacy.read_bytes()),
            "challenge": digest(challenge.read_bytes()),
        },
        "source_sha256": {
            p: digest(Path(p).read_bytes())
            for p in ["scripts/coverage_gate_v2.py", "scripts/replay_gate_v2.py"]
        },
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "timing_scope": "v2 selection only, single sample, no inference/IO/scoring, no latency claim",
        "suites": {},
    }
    for w in ("20", "40"):
        rows = []
        for c, case in zip(old["windows"][w]["cases"], inputs.cases, strict=True):
            rows.append(
                {
                    "case_id": c["case_id"],
                    "query": c["query"],
                    "control": c["control"],
                    "base": c["baseline_ids"],
                    "frozen": c["selected_ids"],
                    "events": c["events"],
                    "judgments": case["judgments"],
                }
            )
        result["suites"].setdefault("legacy", {})[w] = compare(rows)
        raw = json.loads(files[f"raw-{w}.json"])
        candidates = json.loads(files[f"candidates-{w}.json"])
        require(raw["candidate_k"] == int(w), "window mismatch")
        rows = []
        for c, rec in zip(draft["cases"], raw["case_results"], strict=True):
            require(c["query"] == rec["query"], "query mismatch")
            require(all(x["error"] is None for x in rec["channels"]), "channel errors")
            base = next(
                x["retrieved_chunk_ids"] for x in rec["channels"] if x["channel"] == "reranked"
            )
            frozen, events = select_window(
                base,
                candidates[c["query"]]["hybrid"],
                set(inputs.allowed_chunk_ids),
                links,
                window=int(w),
            )
            rows.append(
                {
                    "case_id": c["case_id"],
                    "query": c["query"],
                    "control": not c["supplement_needed"],
                    "base": base,
                    "frozen": frozen,
                    "events": events,
                    "judgments": [
                        {"chunk_id": j["chunk_id"], "relevance": j["grade"]} for j in c["judgments"]
                    ],
                }
            )
        result["suites"].setdefault("challenge_now_regression", {})[w] = compare(rows)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--legacy-zip", type=Path, required=True)
    p.add_argument("--challenge-zip", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=False)
    try:
        r = run(a.legacy_zip, a.challenge_zip)
    except Exception as e:
        (a.output_dir / "failure.json").write_text(
            json.dumps({"status": "blocked", "error": str(e)}), encoding="utf-8"
        )
        raise
    (a.output_dir / "gate-v2-report.json").write_text(
        json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("completed", a.output_dir / "gate-v2-report.json")


if __name__ == "__main__":
    main()
