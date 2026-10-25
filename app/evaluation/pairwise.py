"""Blinded paired evaluation with deterministic eligibility and seeded statistics."""

import random
import json

from app.evaluation.judge import JudgeInput

IDENTITY_KEYS = ("dataset_sha256", "principal_sha256", "corpus_sha256", "tool_fixture_sha256")


def execution_eligible(candidate, case_id):
    """Require replayable deterministic assertions, not a caller's pass booleans."""
    result = candidate.get("workflow_result", {})
    assertions = result.get("assertions", [])
    if result.get("case_id") != case_id or not assertions:
        return False
    for assertion in assertions:
        if (
            assertion.get("passed") is not True
            or "actual" not in assertion
            or "expected" not in assertion
            or json.dumps(assertion["actual"], sort_keys=True)
            != json.dumps(assertion["expected"], sort_keys=True)
        ):
            return False
    names = [a["name"] for a in assertions]
    required = (".error", ".side_effect_count", ".duplicate_side_effect", ".persisted_phase")
    if not all(any(n.endswith(suffix) for n in names) for suffix in required):
        return False
    if any(a["actual"] for a in assertions if a["name"].endswith(".side_effect_count")):
        return all(
            any(n.endswith(suffix) for n in names)
            for suffix in (".confirmation_compliance", ".submission_persisted")
        )
    return True


async def compare_pair(
    judge, *, case_id, query, evidence, candidate_a, candidate_b, seed=0, workflow_case_id=None
):
    for key in IDENTITY_KEYS:
        if not candidate_a["manifest"].get(key) or candidate_a["manifest"][key] != candidate_b[
            "manifest"
        ].get(key):
            raise ValueError("paired experiment identity mismatch: " + key)
    # Eligibility is an explicit deterministic result, not the Judge's opinion.
    eligible = [
        execution_eligible(c, workflow_case_id or case_id) for c in (candidate_a, candidate_b)
    ]
    if not all(eligible):
        return {
            "case_id": case_id,
            "winner": "Unknown",
            "status": "ineligible",
            "order_consistent": None,
            "judge_failures": 0,
            "judge_calls": 0,
        }
    answers = [candidate_a["answer"], candidate_b["answer"]]
    order = [0, 1]
    random.Random(seed).shuffle(order)
    winners, results = [], []
    for positions in (order, order[::-1]):
        result = await judge.evaluate(
            JudgeInput(
                case_id=case_id,
                rubric="pairwise",
                query=query,
                evidence=evidence,
                answer=answers[positions[0]],
                other_answer=answers[positions[1]],
            )
        )
        preference = result.decision.preference if result.status == "ok" else "Unknown"
        if preference in ("A", "B"):
            preference = "AB"[positions[0 if preference == "A" else 1]]
        winners.append(preference)
        results.append(result.model_dump(mode="json"))
    consistent = winners[0] == winners[1] if "Unknown" not in winners else None
    return {
        "case_id": case_id,
        "winner": winners[0] if consistent else "Unknown",
        "status": "evaluated",
        "order_consistent": consistent,
        "judge_failures": sum(r["status"] != "ok" for r in results),
        "judge_calls": 2,
        "judge_results": results,
    }


def summarize_pairs(rows, *, seed=0, bootstrap_samples=2000):
    if len({r["case_id"] for r in rows}) != len(rows):
        raise ValueError("duplicate case IDs invalidate paired statistics")
    n = len(rows)
    counts = {k: sum(r["winner"] == k for r in rows) for k in ("A", "B", "Tie", "Unknown")}
    valid_order = [r["order_consistent"] for r in rows if r["order_consistent"] is not None]
    calls = sum(r["judge_calls"] for r in rows)
    interval = None
    # CI uses all paired cases as denominator, including unknowns; never silently drops failures.
    if n >= 20:
        rng = random.Random(seed)
        values = [r["winner"] == "A" for r in rows]
        samples = sorted(sum(rng.choices(values, k=n)) / n for _ in range(bootstrap_samples))
        interval = [
            samples[int(0.025 * bootstrap_samples)],
            samples[int(0.975 * bootstrap_samples)],
        ]
    return {
        "n": n,
        "counts": counts,
        "rates": {k: v / n if n else None for k, v in counts.items()},
        "order_consistency": sum(valid_order) / len(valid_order) if valid_order else None,
        "judge_failure_rate": sum(r["judge_failures"] for r in rows) / calls if calls else None,
        "a_win_rate_ci95": interval,
        "bootstrap_seed": seed,
        "minimum_ci_cases": 20,
        "denominator": "all_pairs_including_unknown",
    }
