"""Replay frozen coverage rules on authenticated-by-hash local experiment evidence."""

import argparse
import json
import math
from pathlib import Path
from time import perf_counter

from scripts.run_retrieval_coverage_ablation import (
    build_links,
    canonical_digest,
    digest,
    measure,
    read_archive,
    require,
    select_coverage,
    summarize,
    unique_ids,
)
from scripts.run_retrieval_coverage_confirmed_eval import (
    DEFAULT_REVIEW_DIR,
    load_confirmed_inputs,
)


def select_window(baseline, candidates, allowed, links, *, window):
    """Explicit experimental window; same anchor/order/replacement policy as frozen selector."""
    require(type(window) is int and window in (20, 40), "window must be 20 or 40")
    unique_ids(candidates, allowed, window)
    unique_ids(baseline, set(candidates), 5)
    events = []
    for anchor in baseline[:2]:
        for link in links:
            if link["source"] != anchor:
                continue
            target = link["target"]
            event = {"rule_id": link["rule_id"], "anchor": anchor, "target": target}
            if target in baseline:
                events.append({**event, "action": "already_present"})
            elif target not in candidates or target not in allowed:
                events.append({**event, "action": "outside_authorized_candidate_window"})
            else:
                events.append({**event, "action": "supplemented", "evicted": baseline[4:]})
                return baseline[:4] + [target], events
    return list(baseline), events


def replay(archive: Path, review_dir: Path):
    inputs = load_confirmed_inputs(review_dir)
    files = read_archive(archive)

    def read(name):
        return json.loads(files[name])

    evidence = read("candidate-window-report.json")
    require(
        evidence["status"] == "completed"
        and evidence["mode"] == "bge"
        and evidence["real_model_inference"] is True,
        "completed BGE evidence required",
    )
    for key, name in (
        ("source_sha256", "source-manifest.json"),
        ("protocol_sha256", "protocol.json"),
        ("environment_sha256", "environment.json"),
        ("model_lock_sha256", "models.lock.json"),
    ):
        require(canonical_digest(read(name)) == evidence[key], f"fingerprint mismatch: {name}")
    require(read("review.user-confirmed.json") == inputs.review, "labels changed")
    require(read("rules.json") == inputs.rules, "rules changed")
    require(read("corpus.json") == inputs.corpus, "corpus/ACL changed")
    require(read("models.lock.json") == inputs.freeze["model_lock"], "models changed")
    protocol = read("protocol.json")
    require(
        protocol["input"]["confirmed_review_sha256"] == canonical_digest(inputs.review),
        "protocol labels mismatch",
    )
    require(
        evidence["configuration"]["final_k"] == 5
        and evidence["configuration"]["rrf_rank_constant"] == 60,
        "configuration mismatch",
    )
    links = build_links(inputs.corpus["chunks"], inputs.rules)
    output = {
        "status": "completed",
        "mode": "captured_bge_ranking_replay",
        "new_model_inference": False,
        "numeric_resume_ready": False,
        "input_zip_sha256": digest(archive.read_bytes()),
        "source_experiment": evidence["git"],
        "labels_sha256": canonical_digest(inputs.review),
        "rules_sha256": canonical_digest(inputs.rules),
        "selector_sha256": digest(Path(__file__).read_bytes()),
        "timing_scope": "selection only; one sample per query; excludes inference and IO",
        "windows": {},
    }
    for window in (20, 40):
        require(
            digest(files[f"window-{window}/target-dataset.jsonl"]) == evidence["dataset_sha256"],
            "dataset mismatch",
        )
        report = read(f"window-{window}/retrieval-evaluation-report.json")
        require(report["candidate_k"] == window and report["ks"] == [1, 3, 5], "window/K mismatch")
        traces = read(f"window-{window}/candidate-traces.json")
        require(len(report["case_results"]) == len(inputs.cases), "case count mismatch")
        rows = []
        for case, result in zip(inputs.cases, report["case_results"], strict=True):
            require(
                all(case[k] == result[k] for k in ("case_id", "query", "judgments")),
                "case/label mismatch",
            )
            channels = {r["channel"]: r for r in result["channels"]}
            require(set(channels) == {"vector", "bm25", "hybrid", "reranked"}, "channels mismatch")
            require(all(r["error"] is None for r in channels.values()), "channel failure")
            baseline = channels["reranked"]["retrieved_chunk_ids"]
            pool = traces[case["query"]]["hybrid"]
            require(channels["hybrid"]["retrieved_chunk_ids"] == pool[:5], "RRF trace mismatch")
            started = perf_counter()
            selected, events = select_window(
                baseline, pool, set(inputs.allowed_chunk_ids), links, window=window
            )
            elapsed = (perf_counter() - started) * 1000
            if window == 20:
                require(
                    (selected, events)
                    == select_coverage(baseline, pool, set(inputs.allowed_chunk_ids), links),
                    "frozen selector parity failed",
                )
            before, after = (
                measure(baseline, case["judgments"]),
                measure(selected, case["judgments"]),
            )
            require(
                math.isclose(before["recall_at_5"], channels["reranked"]["recall_at_k"]["5"])
                and math.isclose(before["mrr_at_5"], channels["reranked"]["reciprocal_rank"]),
                "baseline metric mismatch",
            )
            control = any(
                t.startswith("unnecessary_") or t == "negative_control" for t in case["tags"]
            )
            rows.append(
                {
                    "case_id": case["case_id"],
                    "query": case["query"],
                    "control": control,
                    "before": before,
                    "after": after,
                    "baseline_ids": baseline,
                    "selected_ids": selected,
                    "events": events,
                    "evicted_ids": [i for i in baseline if i not in selected],
                    "evicted_judgments": [
                        j
                        for j in case["judgments"]
                        if j["chunk_id"] in baseline and j["chunk_id"] not in selected
                    ],
                    "regressed_metrics": [k for k in before if after[k] < before[k] - 1e-12],
                    "selection_ms": elapsed,
                }
            )
        controls = [r for r in rows if r["control"]]
        output["windows"][str(window)] = {
            "before": summarize(rows, "before"),
            "after": summarize(rows, "after"),
            "changed": [r["case_id"] for r in rows if r["baseline_ids"] != r["selected_ids"]],
            "regressions": [r["case_id"] for r in rows if r["regressed_metrics"]],
            "controls": {
                "case_ids": [r["case_id"] for r in controls],
                "before": summarize(controls, "before"),
                "after": summarize(controls, "after"),
                "changed": [
                    r["case_id"]
                    for r in controls
                    if r["events"] and r["baseline_ids"] != r["selected_ids"]
                ],
            },
            "mean_selection_ms": sum(r["selection_ms"] for r in rows) / len(rows),
            "cases": rows,
        }
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-zip", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    try:
        result = replay(args.evidence_zip, args.review_dir)
    except Exception as exc:
        (args.output_dir / "failure.json").write_text(
            json.dumps({"status": "blocked", "error": str(exc)}), encoding="utf-8"
        )
        raise
    (args.output_dir / "replay-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {w: {k: v for k, v in s.items() if k != "cases"} for w, s in result["windows"].items()},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
