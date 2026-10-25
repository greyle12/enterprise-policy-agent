"""Review labels below are synthetic test fixtures, never human review evidence."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.calibrate_saved_judge import run

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def packet(tmp_path):
    source = ROOT / "tests/evaluation/semantic_smoke.jsonl"
    inputs = [json.loads(s) for s in source.read_text("utf-8").splitlines() if s.strip()]
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = {
        "kind": "semantic",
        "mode": "offline",
        "input_sha256": digest,
        "evidence_sha256": hashlib.sha256(
            json.dumps([r["evidence"] for r in inputs], sort_keys=True).encode()
        ).hexdigest(),
        "repetitions": 1,
        "model_version": "synthetic-test-only",
        "prompt_version": "fixture",
        "results": [
            {
                "case_id": row["case_id"],
                "repetition": 0,
                "status": "ok",
                "decision": {
                    "verdict": verdict,
                    "rationale": "fixture",
                    "evidence_ids": list(row["evidence"]),
                    "preference": "Unknown",
                },
                "model_version": "synthetic-test-only",
                "evidence_mode": "offline_mock",
                "latency_ms": 0,
            }
            for row, verdict in zip(inputs, ["PASS", "FAIL", "FAIL"], strict=True)
        ],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), "utf-8")
    report_hash = hashlib.sha256(report_path.read_bytes()).hexdigest()
    labels = [
        {
            **r,
            "repetition": 0,
            "input_sha256": digest,
            "judge_report_sha256": report_hash,
            "answer_origin": "synthetic_test_fixture",
            "human": None,
            "reviewer": None,
            "rationale": None,
            "review_status": "pending_review",
            "reviewer_decision": None,
        }
        for r in inputs
    ]
    return source, report_path, labels, tmp_path


def execute(packet, assistant=None):
    source, report, labels, folder = packet
    label_path = folder / "labels.jsonl"
    label_path.write_text("".join(json.dumps(row) + "\n" for row in labels), "utf-8")
    return run(report, source, label_path, folder / "output", assistant)


def reviewed(row, human="PASS", visibility="seen"):
    row.update(
        human=human,
        reviewer="synthetic-fixture-not-a-human",
        rationale="synthetic test only",
        review_status="human_reviewed",
        judge_visibility=visibility,
    )


def test_pending_does_not_create_human_accuracy(packet):
    result = execute(packet)
    assert result["human_reviewed_samples"] == result["calibration"]["n"] == 0
    assert result["pending_or_missing_reviews"] == 3
    assert result["calibration"]["agreement"] is None
    assert result["additional_model_calls"] == 0


def test_visibility_strata_and_unknown_exclusion(packet):
    for row, human, visibility in zip(
        packet[2], ["PASS", "FAIL", "UNKNOWN"], ["seen", "not_seen", "seen"], strict=True
    ):
        reviewed(row, human, visibility)
    result = execute(packet)
    assert result["human_reviewed_samples"] == 3
    assert result["calibration"]["n"] == 2
    assert result["calibration"]["agreement"] == 1
    assert result["judge_not_seen_calibration"]["n"] == 1
    assert result["judge_seen_calibration"]["n"] == 1


@pytest.mark.parametrize(
    "fault",
    [
        "report_hash",
        "input_hash",
        "duplicate",
        "unknown_case",
        "content",
        "blank_reviewer",
        "visibility",
        "extra_judge",
    ],
)
def test_bad_labels_rejected_before_output(packet, fault):
    row = packet[2][0]
    reviewed(row)
    if fault == "report_hash":
        row["judge_report_sha256"] = "0" * 64
    elif fault == "input_hash":
        row["input_sha256"] = "0" * 64
    elif fault == "duplicate":
        packet[2].append(dict(row))
    elif fault == "unknown_case":
        row["repetition"] = 100
    elif fault == "content":
        row["answer"] = "altered after review"
    elif fault == "blank_reviewer":
        row["reviewer"] = " "
    elif fault == "visibility":
        row["judge_visibility"] = "unknown"
    else:
        row["judge"] = "PASS"
    with pytest.raises(ValueError):
        execute(packet)
    assert not (packet[3] / "output").exists()


def test_provider_error_is_excluded_even_if_report_says_pass(packet):
    report_path = packet[1]
    report = json.loads(report_path.read_text("utf-8"))
    report["results"][0]["status"] = "provider_error"
    report_path.write_text(json.dumps(report), "utf-8")
    for row in packet[2]:
        row["judge_report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    reviewed(packet[2][0])
    result = execute(packet)
    assert result["calibration"]["n"] == 0
    assert result["results"][0]["judge"] == "UNKNOWN"


@pytest.mark.parametrize("fault", ["duplicate", "missing", "wrong_input"])
def test_report_binding_and_coverage(packet, fault):
    path = packet[1]
    report = json.loads(path.read_text("utf-8"))
    if fault == "duplicate":
        report["results"].append(report["results"][0])
    elif fault == "missing":
        report["results"].pop()
    else:
        report["input_sha256"] = "0" * 64
    path.write_text(json.dumps(report), "utf-8")
    with pytest.raises(ValueError):
        execute(packet)


def test_output_cannot_overwrite_evidence(packet):
    output = packet[3] / "output"
    output.mkdir()
    marker = output / "marker"
    marker.write_text("preserve", "utf-8")
    with pytest.raises(FileExistsError):
        execute(packet)
    assert marker.read_text("utf-8") == "preserve"


def assistant_packet(packet):
    source, report, labels, folder = packet
    rows = [
        {
            "case_id": row["case_id"],
            "rubric": row["rubric"],
            "assistant_verdict": verdict,
            "evidence_ids": list(row["evidence"]),
            "rationale": "synthetic diagnostic fixture",
            "reviewer_type": "assistant",
            "review_status": "assistant_reviewed",
            "judge_visibility": "seen",
            "human": None,
            "reviewer_decision": None,
            "eligible_for_human_calibration": False,
        }
        for row, verdict in zip(labels, ["PASS", "FAIL", "FAIL"], strict=True)
    ]
    return {
        "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "judge_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        "results": rows,
    }


def write_assistant(packet, review):
    path = packet[3] / "assistant.json"
    path.write_text(json.dumps(review), "utf-8")
    return path


def test_assistant_matches_never_enter_human_calibration(packet):
    review = assistant_packet(packet)
    result = execute(packet, write_assistant(packet, review))
    assert result["assistant_comparison"]["matching_binary"] == 3
    assert result["assistant_comparison"]["eligible_for_human_calibration"] is False
    assert result["human_reviewed_samples"] == result["calibration"]["n"] == 0
    assert result["calibration"]["agreement"] is None


@pytest.mark.parametrize(
    "fault", ["hash", "duplicate", "human", "eligibility", "rubric", "evidence"]
)
def test_assistant_bad_provenance_rejected(packet, fault):
    review = assistant_packet(packet)
    row = review["results"][0]
    if fault == "hash":
        review["judge_report_sha256"] = "0" * 64
    elif fault == "duplicate":
        review["results"].append(dict(row))
    elif fault == "human":
        row["human"] = "PASS"
    elif fault == "eligibility":
        row["eligible_for_human_calibration"] = True
    elif fault == "rubric":
        row["rubric"] = "completeness"
    else:
        row["evidence_ids"] = ["unprovided"]
    with pytest.raises(ValueError):
        execute(packet, write_assistant(packet, review))
    assert not (packet[3] / "output").exists()


def test_assistant_unknown_not_counted_as_match(packet):
    review = assistant_packet(packet)
    review["results"][0]["assistant_verdict"] = "UNKNOWN"
    review["results"][1]["assistant_verdict"] = "PASS"
    result = execute(packet, write_assistant(packet, review))["assistant_comparison"]
    assert (result["matching_binary"], result["different_binary"], result["unscorable"]) == (
        1,
        1,
        1,
    )
