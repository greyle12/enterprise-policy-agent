"""Local, pending-review failure intake. Never creates trusted labels automatically."""

import hashlib
import json
from pathlib import Path

CATEGORIES = (
    "retrieval",
    "context",
    "llm_reasoning",
    "tool_selection",
    "tool_argument",
    "business_rule",
    "state_transition",
    "permission",
    "judge",
    "infrastructure",
)


def failure_cards(results):
    cards = []
    for result in results:
        if result["passed"]:
            continue
        failed = [a["name"] for a in result["assertions"] if not a["passed"]]
        categories = set()
        for name in failed:
            if "error" in name or "result_present" in name:
                categories.add("infrastructure")
            elif "authorization" in name:
                categories.add("permission")
            elif "citation" in name or "abstention" in name:
                categories.add("context")
            elif "field." in name:
                categories.add("business_rule")
            elif "tool_selection" in name:
                categories.add("tool_selection")
            elif "tool_arguments" in name:
                categories.add("tool_argument")
            elif "confirmation" in name or "side_effect" in name:
                categories.add("business_rule")
            else:
                categories.add("state_transition")
        cards.append(
            {
                "case_id": result["case_id"],
                "source_split": result["split"],
                "failure_categories": sorted(categories),
                "failed_assertions": failed,
                "source_sha256": hashlib.sha256(
                    json.dumps(result, sort_keys=True).encode()
                ).hexdigest(),
                "review_status": "pending_review",
                "review_mode": "assistant_assisted_human_review",
                "reviewer_decision": None,
                "attribution_status": "automatic_diagnostic",
                "execution_id": result.get("execution_id"),
            }
        )
    return cards


def approve_regression(card, *, reviewer, decision, rationale):
    if card["source_split"] == "holdout":
        raise ValueError("independent holdout cannot enter tuning/regression intake")
    if not reviewer.strip() or decision != "approve" or not rationale.strip():
        raise ValueError("explicit human review required")
    return {
        **card,
        "review_status": "human_reviewed",
        "reviewer": reviewer,
        "reviewer_decision": decision,
        "review_rationale": rationale,
        "target_split": "regression",
    }


def write_pending_cards(results, path):
    Path(path).write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in failure_cards(results)),
        encoding="utf-8",
    )


def regression_summary(previous, current):
    if previous["manifest"]["dataset_sha256"] != current["manifest"]["dataset_sha256"]:
        raise ValueError("regression comparison requires identical dataset")
    before = {r["case_id"]: r["passed"] for r in previous["results"]}
    after = {r["case_id"]: r["passed"] for r in current["results"]}
    if before.keys() != after.keys():
        raise ValueError("regression comparison case inventory mismatch")
    return {
        "regressions": sorted(k for k in after if before[k] and not after[k]),
        "improvements": sorted(k for k in after if not before[k] and after[k]),
        "unchanged": sum(before[k] == after[k] for k in after),
    }
