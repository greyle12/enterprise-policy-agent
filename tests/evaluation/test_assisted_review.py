import hashlib
import json

import pytest

from app.evaluation.judge import calibration
from scripts.prepare_assisted_review import run
from scripts.calibrate_saved_judge import ReviewRow


@pytest.fixture
def packet(tmp_path):
    evidence = [
        {
            "source_id": "S1",
            "content": "A receipt is required.",
            "document_title": "Synthetic",
            "document_version": "1.0",
            "article": "Purpose",
        }
    ]
    capture = {
        "provider_mode": "offline_scripted",
        "results": [
            {
                "case_id": "WF-013",
                "execution_id": "fixture-exec",
                "turns": [
                    {
                        "turn": 0,
                        "query": "Requirements?",
                        "result": {
                            "status": "completed",
                            "classification": {"intent": "policy_query"},
                            "reply": "A receipt is required. [S1]",
                        },
                        "provider_records": [
                            {
                                "status": "ok",
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": "question\n<policy_evidence_json>"
                                        + json.dumps(evidence)
                                        + "</policy_evidence_json>",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(capture), "utf-8")
    recommendations = {
        "source_capture_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "reviewer_type": "assistant",
        "recommendations": [
            {
                "case_id": "WF-013-T0-faithfulness",
                "verdict": "PASS",
                "rationale": "Matches S1; synthetic test only.",
                "evidence_ids": ["S1"],
                "suggested_action": "keep",
            }
        ],
    }
    return path, recommendations, tmp_path


def execute(packet):
    source, recommendations, folder = packet
    path = folder / "recommendations.json"
    path.write_text(json.dumps(recommendations), "utf-8")
    return run(source, path, folder / "output")


def test_assisted_packet_binds_answer_metadata_and_keeps_human_separate(packet):
    summary = execute(packet)
    assert summary["assistant_reviewed_samples"] == 1
    assert summary["human_confirmed_samples"] == summary["additional_model_calls"] == 0
    row = json.loads((packet[2] / "output/assisted-review.jsonl").read_text("utf-8"))
    assert row["assistant_recommendation"] == "PASS" and row["human"] is None
    assert row["reviewer_decision"] is None
    assert json.loads(row["evidence"]["S1"])["document_version"] == "1.0"
    assert row["execution_id"] == "fixture-exec"
    assert calibration([row])["n"] == 0
    with pytest.raises(ValueError):
        ReviewRow.model_validate(row)


@pytest.mark.parametrize(
    "fault",
    [
        "hash",
        "duplicate",
        "missing",
        "unknown",
        "evidence",
        "human",
        "blank_rationale",
        "reviewer_type",
    ],
)
def test_invalid_assistant_input_does_not_write_output(packet, fault):
    rec = packet[1]["recommendations"][0]
    if fault == "hash":
        packet[1]["source_capture_sha256"] = "0" * 64
    elif fault == "duplicate":
        packet[1]["recommendations"].append(dict(rec))
    elif fault == "missing":
        packet[1]["recommendations"] = []
    elif fault == "unknown":
        rec["case_id"] = "unknown"
    elif fault == "evidence":
        rec["evidence_ids"] = ["invented"]
    elif fault == "human":
        rec["human"] = "PASS"
    elif fault == "blank_rationale":
        rec["rationale"] = " "
    else:
        packet[1]["reviewer_type"] = "human"
    with pytest.raises(ValueError):
        execute(packet)
    assert not (packet[2] / "output").exists()


def test_existing_review_is_preserved(packet):
    execute(packet)
    summary = packet[2] / "output/summary.json"
    before = summary.read_bytes()
    with pytest.raises(FileExistsError):
        execute(packet)
    assert summary.read_bytes() == before
