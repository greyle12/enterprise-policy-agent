"""Join explicit human reviews to a saved Judge report without provider calls."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from app.evaluation.judge import JudgeInput, JudgeResult, calibration


class ReviewRow(JudgeInput):
    review_mode: Literal["assistant_assisted_human_review"] = "assistant_assisted_human_review"
    repetition: StrictInt = Field(ge=0)
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    judge_report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    answer_origin: str
    human: Literal["PASS", "FAIL", "UNKNOWN"] | None = None
    reviewer: str | None = None
    rationale: str | None = None
    review_status: Literal["pending_review", "human_reviewed"] = "pending_review"
    judge_visibility: Literal["unknown", "not_seen", "seen"] = "unknown"
    reviewer_decision: None = None

    @model_validator(mode="after")
    def explicit_review(self):
        if self.review_status == "human_reviewed":
            if (
                self.human is None
                or not self.reviewer
                or not self.reviewer.strip()
                or not self.rationale
                or not self.rationale.strip()
                or self.judge_visibility == "unknown"
            ):
                raise ValueError(
                    "completed review requires label, reviewer, rationale and Judge visibility"
                )
        elif any(value is not None for value in (self.human, self.reviewer, self.rationale)):
            raise ValueError("pending review cannot contain completed label fields")
        return self


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def compare_assistant(path, report_hash, input_hash, inputs, observed):
    packet = json.loads(path.read_text("utf-8"))
    if packet.get("judge_report_sha256") != report_hash or packet.get("input_sha256") != input_hash:
        raise ValueError("assistant review fingerprint mismatch")
    comparisons, keys = [], set()
    for row in packet["results"]:
        repetition = row.get("repetition", 0)
        if type(repetition) is not int:
            raise ValueError("invalid assistant repetition")
        key = row["case_id"], repetition
        if key in keys or key not in observed:
            raise ValueError("duplicate or unknown assistant review")
        keys.add(key)
        if (
            row.get("reviewer_type") != "assistant"
            or row.get("review_status") != "assistant_reviewed"
            or row.get("human") is not None
            or row.get("reviewer_decision") is not None
            or row.get("eligible_for_human_calibration") is not False
            or row.get("assistant_verdict") not in {"PASS", "FAIL", "UNKNOWN"}
            or row.get("judge_visibility") not in {"seen", "not_seen", "unknown"}
            or row.get("rubric") != inputs[row["case_id"]].rubric
            or not set(row.get("evidence_ids", [])) <= set(inputs[row["case_id"]].evidence)
            or not isinstance(row.get("rationale"), str)
            or not row["rationale"].strip()
        ):
            raise ValueError("invalid assistant review provenance or content")
        judge = observed[key]
        comparable = judge.status == "ok" and "UNKNOWN" not in {
            judge.decision.verdict,
            row["assistant_verdict"],
        }
        comparisons.append(
            {
                "case_id": row["case_id"],
                "repetition": repetition,
                "assistant_verdict": row["assistant_verdict"],
                "judge_verdict": judge.decision.verdict,
                "judge_status": judge.status,
                "judge_visibility": row["judge_visibility"],
                "matches": row["assistant_verdict"] == judge.decision.verdict
                if comparable
                else None,
            }
        )
    return {
        "source_sha256": sha(path),
        "reviewed": len(comparisons),
        "matching_binary": sum(r["matches"] is True for r in comparisons),
        "different_binary": sum(r["matches"] is False for r in comparisons),
        "unscorable": sum(r["matches"] is None for r in comparisons),
        "eligible_for_human_calibration": False,
        "scope": "assistant diagnostics; no accuracy or independent review claim",
        "results": comparisons,
    }


def run(report_path, input_path, labels_path, output, assistant_review_path=None):
    if output.exists():
        raise FileExistsError(output)
    report = json.loads(report_path.read_text("utf-8"))
    report_hash, input_hash = sha(report_path), sha(input_path)
    if report.get("kind") != "semantic" or report.get("input_sha256") != input_hash:
        raise ValueError("semantic report/input binding mismatch")
    inputs = [JudgeInput.model_validate(row) for row in rows(input_path)]
    by_id = {row.case_id: row for row in inputs}
    if not inputs or len(by_id) != len(inputs) or any(row.rubric == "pairwise" for row in inputs):
        raise ValueError("empty, duplicate or pairwise semantic inputs")
    evidence_hash = hashlib.sha256(
        json.dumps([row.evidence for row in inputs], sort_keys=True).encode()
    ).hexdigest()
    if evidence_hash != report.get("evidence_sha256"):
        raise ValueError("evidence binding mismatch")
    repetitions = report.get("repetitions")
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError("invalid repetitions")
    observed = {}
    for row in report["results"]:
        result = JudgeResult.model_validate(
            {k: v for k, v in row.items() if k not in {"case_id", "repetition"}}
        )
        if type(row["repetition"]) is not int:
            raise ValueError("invalid result repetition")
        key = row["case_id"], row["repetition"]
        if key in observed:
            raise ValueError("duplicate observed result")
        observed[key] = result
    expected = {(case_id, r) for case_id in by_id for r in range(repetitions)}
    if set(observed) != expected:
        raise ValueError("report result coverage mismatch")
    reviews = [ReviewRow.model_validate(row) for row in rows(labels_path)]
    joined, keys = [], set()
    for review in reviews:
        key = review.case_id, review.repetition
        if key in keys or key not in observed:
            raise ValueError("duplicate or unknown review case/repetition")
        keys.add(key)
        if review.input_sha256 != input_hash or review.judge_report_sha256 != report_hash:
            raise ValueError("review fingerprint mismatch")
        original = by_id[review.case_id]
        if any(
            getattr(review, field) != getattr(original, field) for field in JudgeInput.model_fields
        ):
            raise ValueError("review content differs from bound input")
        result = observed[key]
        joined.append(
            {
                "case_id": review.case_id,
                "repetition": review.repetition,
                "human": review.human,
                "reviewer": review.reviewer,
                "rationale": review.rationale,
                "review_status": review.review_status,
                "review_mode": review.review_mode,
                "judge_visibility": review.judge_visibility,
                "judge": result.decision.verdict if result.status == "ok" else "UNKNOWN",
                "judge_status": result.status,
            }
        )
    completed = [r for r in joined if r["review_status"] == "human_reviewed"]
    summary = {
        "source_report_sha256": report_hash,
        "input_sha256": input_hash,
        "labels_sha256": sha(labels_path),
        "source_mode": report["mode"],
        "source_model_version": report["model_version"],
        "source_prompt_version": report["prompt_version"],
        "source_results": len(observed),
        "provided_reviews": len(reviews),
        "pending_or_missing_reviews": len(observed) - len(completed),
        "human_reviewed_samples": len(completed),
        "calibration": calibration(joined),
        "judge_not_seen_calibration": calibration(
            [r for r in joined if r["judge_visibility"] == "not_seen"]
        ),
        "judge_seen_calibration": calibration(
            [r for r in joined if r["judge_visibility"] == "seen"]
        ),
        "status": "human_labels_supplied_not_certified" if completed else "pending_human_review",
        "additional_model_calls": 0,
        "review_provenance": "operator supplied; reviewer identity and independence are not authenticated",
        "results": joined,
    }
    if assistant_review_path:
        summary["assistant_comparison"] = compare_assistant(
            assistant_review_path, report_hash, input_hash, by_id, observed
        )
    output.mkdir(parents=True)
    (output / "calibration-report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    (output / "calibration-report.md").write_text(
        "# Saved Judge calibration\n\n"
        f"Status: {summary['status']}. Human reviewed: {len(completed)}; binary n: {summary['calibration']['n']}.\n\n"
        "No additional model calls. See JSON for pending, UNKNOWN, provider errors and Judge visibility strata.\n"
        "Optional assistant comparisons are diagnostics and do not enter human calibration.\n"
        "Human provenance is operator supplied; small smoke fixtures do not establish deployment readiness.\n",
        "utf-8",
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--assistant-review", type=Path)
    args = parser.parse_args()
    summary = run(args.report, args.input, args.labels, args.output_dir, args.assistant_review)
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
