"""Bind assistant recommendations to captured answers for optional human confirmation."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.evaluation.models import _StrictModel
from scripts.run_agent_workflow_capture import export_semantic


class Recommendation(_StrictModel):
    case_id: str
    verdict: Literal["PASS", "FAIL", "UNKNOWN"]
    rationale: str = Field(min_length=1)
    evidence_ids: tuple[str, ...]
    suggested_action: Literal["keep", "revise", "request_evidence"]
    concerns: tuple[str, ...] = ()

    @model_validator(mode="after")
    def evidence_required(self):
        if not self.rationale.strip() or (self.verdict != "UNKNOWN" and not self.evidence_ids):
            raise ValueError("explicit rationale and supporting evidence required")
        return self


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(capture_path, recommendations_path, output):
    if output.exists():
        raise FileExistsError(output)
    capture = json.loads(capture_path.read_text("utf-8"))
    items, bindings, skipped = export_semantic(capture["results"])
    by_id = {item["case_id"]: item for item in items}
    bound = {row["case_id"]: row for row in bindings}
    packet = json.loads(recommendations_path.read_text("utf-8"))
    if (
        packet.get("source_capture_sha256") != sha(capture_path)
        or packet.get("reviewer_type") != "assistant"
    ):
        raise ValueError("assistant source binding mismatch")
    recommendations = [Recommendation.model_validate(row) for row in packet["recommendations"]]
    keys = [row.case_id for row in recommendations]
    if not recommendations or len(keys) != len(set(keys)) or set(keys) != set(by_id):
        raise ValueError("duplicate, missing or unknown recommendation case")
    reviews = []
    for rec in recommendations:
        item, binding = by_id[rec.case_id], bound[rec.case_id]
        if not set(rec.evidence_ids) <= set(item["evidence"]):
            raise ValueError("recommendation cites unavailable evidence")
        reviews.append(
            {
                **item,
                "evidence": {
                    key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                    for key, value in binding["authorized_evidence_records"].items()
                },
                "source_capture_sha256": sha(capture_path),
                "execution_id": binding["execution_id"],
                "answer_sha256": binding["answer_sha256"],
                "review_mode": "assistant_assisted_human_review",
                "review_status": "assistant_assistance_complete_pending_human_confirmation",
                "assistant_reviewer": "Codex assistant",
                "assistant_recommendation": rec.verdict,
                "assistant_rationale": rec.rationale,
                "assistant_evidence_ids": list(rec.evidence_ids),
                "suggested_action": rec.suggested_action,
                "concerns": list(rec.concerns),
                "human": None,
                "reviewer": None,
                "reviewer_decision": None,
                "eligible_for_human_calibration": False,
                "semantic_judge_status": "not_run",
            }
        )
    summary = {
        "review_mode": "assistant_assisted_human_review",
        "source_capture_sha256": sha(capture_path),
        "recommendations_sha256": sha(recommendations_path),
        "source_provider_mode": capture["provider_mode"],
        "assistant_reviewed_samples": len(reviews),
        "human_confirmed_samples": 0,
        "suggestions": {
            v: sum(r["assistant_recommendation"] == v for r in reviews)
            for v in ["PASS", "FAIL", "UNKNOWN"]
        },
        "additional_model_calls": 0,
        "semantic_judge_calls": 0,
        "unreviewed_other_turns": len(skipped),
        "calibration_agreement": None,
        "model_quality_certified": False,
    }
    output.mkdir(parents=True)
    (output / "assisted-review.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in reviews), "utf-8"
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    markdown = (
        "# 助手辅助人工审核\n\n助手先提供建议与依据，人工可按需确认；助手建议不计入人工校准。\n\n"
    )
    for row in reviews:
        markdown += (
            f"## {row['case_id']}\n\n问题：{row['query']}\n\n原回答：\n\n> "
            + row["answer"].replace("\n", "\n> ")
            + "\n\n"
        )
        markdown += f"助手建议：**{row['assistant_recommendation']}**（{row['rubric']}）。\n\n依据：{row['assistant_rationale']}\n\n"
        for source, evidence in row["evidence"].items():
            markdown += f"证据 {source}：{evidence}\n\n"
        markdown += (
            "关注点："
            + ("；".join(row["concerns"]) or "未发现本维度的明确问题")
            + "。\n\n人工确认：待确认，可保留、修改或标为 UNKNOWN。\n\n"
        )
    (output / "assisted-review.md").write_text(markdown, "utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--recommendations", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.capture, args.recommendations, args.output_dir), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
