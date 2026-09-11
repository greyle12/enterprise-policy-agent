import copy
import json
from pathlib import Path

import pytest

from scripts.freeze_semantic_facet_blind import (
    build_accepted,
    build_records,
    load_draft,
    validate_draft,
)

ROOT = Path(__file__).resolve().parents[2]
PROPOSAL_PATH = ROOT / "docs/gate_v3/semantic-request-facets-v1/facets.json"


def _inputs():
    questions, review = load_draft(ROOT)
    proposal = json.loads(PROPOSAL_PATH.read_text(encoding="utf-8"))
    validate_draft(questions, review)
    return questions, review, proposal


def test_freeze_preserves_empty_truth_and_source_spans():
    questions, review, proposal = _inputs()
    records = build_records(questions)
    accepted = build_accepted(questions, review, proposal)

    assert len(records) == 18
    assert accepted["accepted_facet_instance_count"] == 17
    assert {case["case_id"] for case in accepted["cases"] if not case["accepted_facets"]} == {
        "SFB-009",
        "SFB-012",
    }
    for record, question in zip(records, questions, strict=True):
        assert record["query"] == question["query"]
        assert record["status"] == "PARTIAL"
    for case in accepted["cases"]:
        for facet in case["accepted_facets"]:
            for span in facet["source_spans"]:
                assert case["query"][span["start"] : span["end"]] == span["text"]


@pytest.mark.parametrize("mutation", ["status", "accepted", "query", "span", "binding"])
def test_freeze_rejects_review_mutations(mutation):
    questions, review, _ = _inputs()
    questions = copy.deepcopy(questions)
    review = copy.deepcopy(review)
    if mutation == "status":
        review["status"] = "user_confirmed"
    elif mutation == "accepted":
        review["cases"][0]["accepted_facets"] = []
    elif mutation == "query":
        questions[0]["query"] = questions[1]["query"]
    elif mutation == "span":
        review["cases"][0]["proposed_facets"][0]["span"]["text"] = "错误 Span"
    else:
        review["cases"][0]["proposed_facets"][0]["binding"] = "MISSING"
    with pytest.raises(ValueError):
        validate_draft(questions, review)
