"""Post-hoc development replay: frozen coverage versus two query-only gates."""

import argparse
import json
import platform
import re
from pathlib import Path
from time import perf_counter

from scripts.replay_coverage_windows import replay
from scripts.run_retrieval_coverage_ablation import digest, measure, summarize
from scripts.run_retrieval_coverage_confirmed_eval import DEFAULT_REVIEW_DIR, load_confirmed_inputs

POLICY = {
    "explicit_veto": {
        "city_category_for_lodging_table": r"合住|同住|[一二三]类城市",
        "finance_review_for_overdue_expense": r"(不需要|不用|不问|无需).{0,6}(审批|流程)",
    },
    "positive_intent": {
        "city_category_for_lodging_table": r"限额|上限|多少钱|多少元|[0-9]+元|住宿标准",
        "finance_review_for_overdue_expense": r"审批|复核|说明|材料|部门|财务",
    },
}


def gate(query, rule_id, variant):
    """No case IDs, tags, relevance labels or judged metrics are inputs."""
    if variant not in ("explicit_veto", "positive_intent"):
        raise ValueError("unknown gate")
    if rule_id not in POLICY["explicit_veto"]:
        raise ValueError("unknown rule")
    if re.search(POLICY["explicit_veto"][rule_id], query):
        return False, "explicit_veto"
    if variant == "positive_intent" and not re.search(POLICY[variant][rule_id], query):
        return False, "positive_intent_absent"
    return True, "allowed"


def select_gated(query, baseline, frozen, events, variant):
    # Veto only the one frozen proposal; never seek another replacement.
    for event in events:
        if event["action"] == "supplemented":
            allowed, reason = gate(query, event["rule_id"], variant)
            return list(frozen if allowed else baseline), reason
    return list(frozen), "no_proposal"


def run(archive, review_dir):
    baseline = replay(archive, review_dir)
    inputs = load_confirmed_inputs(review_dir)
    labels = {c["case_id"]: c["judgments"] for c in inputs.cases}
    output = {
        "status": "completed",
        "experiment": "query_gate_development_v1",
        "new_model_inference": False,
        "numeric_resume_ready": False,
        "policy": POLICY,
        "baseline_evidence": baseline,
        "script_sha256": digest(Path(__file__).read_bytes()),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "timing_scope": "gate selection only, once per query; excludes frozen selection, IO, models and scoring",
        "windows": {},
    }
    for window, data in baseline["windows"].items():
        variants = {}
        for variant in POLICY:
            rows = []
            for source in data["cases"]:
                start = perf_counter()
                selected, reason = select_gated(
                    source["query"],
                    source["baseline_ids"],
                    source["selected_ids"],
                    source["events"],
                    variant,
                )
                elapsed = (perf_counter() - start) * 1000
                judgments = labels[source["case_id"]]
                after = measure(selected, judgments)
                rows.append(
                    {
                        **source,
                        "before": source["after"],
                        "after": after,
                        "ungated_metrics": source["after"],
                        "raw_metrics": source["before"],
                        "frozen_selected_ids": source["selected_ids"],
                        "selected_ids": selected,
                        "frozen_events": source["events"],
                        "gate_reason": reason,
                        "blocked_proposal": selected != source["selected_ids"],
                        "evicted_ids": [i for i in source["baseline_ids"] if i not in selected],
                        "evicted_judgments": [
                            j
                            for j in judgments
                            if j["chunk_id"] in source["baseline_ids"]
                            and j["chunk_id"] not in selected
                        ],
                        "regressed_metrics": [
                            k for k in after if after[k] < source["after"][k] - 1e-12
                        ],
                        "regressions_vs_raw": [
                            k for k in after if after[k] < source["before"][k] - 1e-12
                        ],
                        "selection_ms": elapsed,
                    }
                )
            controls = [r for r in rows if r["control"]]
            variants[variant] = {
                "before": summarize(rows, "before"),
                "after": summarize(rows, "after"),
                "blocked": [r["case_id"] for r in rows if r["blocked_proposal"]],
                "regressions": [r["case_id"] for r in rows if r["regressed_metrics"]],
                "controls": {
                    "before": summarize(controls, "before"),
                    "after": summarize(controls, "after"),
                    "remaining_changes": [
                        r["case_id"] for r in controls if r["baseline_ids"] != r["selected_ids"]
                    ],
                },
                "mean_gate_selection_ms": sum(r["selection_ms"] for r in rows) / len(rows),
                "cases": rows,
            }
        output["windows"][window] = variants
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evidence-zip", type=Path, required=True)
    p.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    try:
        result = run(args.evidence_zip, args.review_dir)
    except Exception as exc:
        (args.output_dir / "failure.json").write_text(
            json.dumps({"status": "blocked", "error": str(exc)}), encoding="utf-8"
        )
        raise
    (args.output_dir / "gate-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("completed:", args.output_dir / "gate-report.json")


if __name__ == "__main__":
    main()
