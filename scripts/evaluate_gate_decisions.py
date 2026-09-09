"""Direct query/rule decisions; diagnostic classification, never retrieval quality."""

import argparse
import json
from pathlib import Path

from scripts.coverage_gate_v2 import decide
from scripts.experiment_coverage_gate import gate
from scripts.run_gate_challenge import load_challenge as load_first
from scripts.run_gate_v2_unseen import load_challenge as load_second
from scripts.run_retrieval_coverage_ablation import digest

CITY = "city_category_for_lodging_table"
FINANCE = "finance_review_for_overdue_expense"
# Explicit rule pairing, not runtime inference from relevance labels.
PAIRS = {
    "CHG-001": FINANCE,
    "CHG-002": CITY,
    "CHG-003": FINANCE,
    "CHG-004": CITY,
    "CHG-006": CITY,
    "CHG-007": CITY,
    "CHG-008": FINANCE,
    "CHG-009": CITY,
    "CHG-010": FINANCE,
    "CHG-011": FINANCE,
    "CHG-012": FINANCE,
    "V2U-001": CITY,
    "V2U-002": CITY,
    "V2U-003": FINANCE,
    "V2U-004": CITY,
    "V2U-005": CITY,
    "V2U-006": FINANCE,
    "V2U-007": FINANCE,
    "V2U-008": FINANCE,
}
EXCLUDED = {
    "CHG-005": "Direct request for city list; city category is primary evidence, not supplemental to a lodging table. Existing supplement_needed=false is not a target-irrelevance label."
}


def metrics(rows, variant):
    tp = sum(r["expected_allow"] and r[variant] for r in rows)
    tn = sum(not r["expected_allow"] and not r[variant] for r in rows)
    fp = sum(not r["expected_allow"] and r[variant] for r in rows)
    fn = sum(r["expected_allow"] and not r[variant] for r in rows)

    def ratio(a, b):
        return a / b if b else None

    return {
        "n": len(rows),
        "tp": tp,
        "tn": tn,
        "fp_unnecessary_allowed": fp,
        "fn_needed_blocked": fn,
        "accuracy": ratio(tp + tn, len(rows)),
        "needed_allow_recall": ratio(tp, tp + fn),
        "unnecessary_block_recall": ratio(tn, tn + fp),
        "allow_precision": ratio(tp, tp + fp),
    }


def evaluate():
    out = {
        "status": "completed",
        "scope": "direct_query_rule_diagnostic",
        "new_model_inference": False,
        "numeric_resume_ready": False,
        "pair_label_status": "assistant_derived_rule_pairs_from_user_confirmed_supplement_labels_not_separately_human_reviewed",
        "assumption": "Hypothetical valid authorized source anchor and missing target in candidate window; evaluate necessity only, not actual retrieval opportunities.",
        "excluded": EXCLUDED,
        "sources": {},
        "suites": {},
    }
    for name, loader in [
        ("challenge_development", load_first),
        ("v2_unseen_now_observed", load_second),
    ]:
        draft, confirmation = loader()
        out["sources"][name] = confirmation
        rows = []
        for c in draft["cases"]:
            cid = c["case_id"]
            if cid in EXCLUDED:
                continue
            rule = PAIRS[cid]
            v1, reason = gate(c["query"], rule, "explicit_veto")
            v2, trace = decide(c["query"], rule)
            rows.append(
                {
                    "case_id": cid,
                    "query": c["query"],
                    "rule_id": rule,
                    "expected_allow": c["supplement_needed"],
                    "label_rationale": c["rationale"],
                    "v1": v1,
                    "v2": v2,
                    "v1_reason": reason,
                    "v2_clauses": trace,
                }
            )
        out["suites"][name] = {
            "metrics": {v: metrics(rows, v) for v in ("v1", "v2")},
            "rows": rows,
            "all_errors": {
                v: [r["case_id"] for r in rows if r[v] != r["expected_allow"]] for v in ("v1", "v2")
            },
        }
    out["source_sha256"] = {
        p: digest(Path(p).read_bytes())
        for p in [
            "scripts/evaluate_gate_decisions.py",
            "scripts/coverage_gate_v2.py",
            "scripts/experiment_coverage_gate.py",
        ]
    }
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=False)
    try:
        result = evaluate()
    except Exception as exc:
        (a.output_dir / "failure.json").write_text(
            json.dumps({"status": "blocked", "error": str(exc)}), encoding="utf-8"
        )
        raise
    (a.output_dir / "decision-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v["metrics"] for k, v in result["suites"].items()}, ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
