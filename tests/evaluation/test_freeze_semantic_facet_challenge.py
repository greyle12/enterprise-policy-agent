import copy
import json
from pathlib import Path

import pytest

from scripts.freeze_semantic_facet_challenge import (
    build_accepted,
    build_records,
    validate_draft,
)

ROOT = Path(__file__).resolve().parents[2]
DRAFT_PATH = ROOT / "docs/gate_v3/semantic-facet-challenge-v1-draft/cases.json"
PROPOSAL_PATH = ROOT / "docs/gate_v3/semantic-request-facets-v1/facets.json"


def _inputs() -> tuple[list[dict], dict]:
    draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
    proposal = json.loads(PROPOSAL_PATH.read_text(encoding="utf-8"))
    validate_draft(draft)
    return draft["cases"], proposal


def test_frozen_mapping_preserves_cases_and_validates_source_spans() -> None:
    cases, proposal = _inputs()
    records = build_records(cases)
    accepted = build_accepted(cases, proposal)

    assert len(records) == 12
    assert len(accepted["cases"]) == 12
    assert accepted["accepted_facet_instance_count"] == 14
    assert accepted["dataset_version"] == "semantic-facet-challenge-v1-confirmed-1"
    for record, case in zip(records, cases, strict=True):
        assert record["query"] == case["query"]
        assert record["status"] == "PARTIAL"
        assert record["missing_outputs"][0]["output_type"] == "request_facet"
    for case in accepted["cases"]:
        for facet in case["accepted_facets"]:
            for span in facet["source_spans"]:
                assert case["query"][span["start"] : span["end"]] == span["text"]


@pytest.mark.parametrize("field", ["review_status", "accepted_facets"])
def test_freeze_rejects_unreviewed_or_preaccepted_draft(field: str) -> None:
    draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
    if field == "review_status":
        draft["cases"][0][field] = "user_confirmed"
    else:
        draft["cases"][0][field] = []
    with pytest.raises(ValueError, match="must not contain accepted labels"):
        validate_draft(draft)


def test_freeze_rejects_duplicate_query_without_mutating_source() -> None:
    draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
    original = copy.deepcopy(draft)
    draft["cases"][1]["query"] = draft["cases"][0]["query"]
    with pytest.raises(ValueError, match="queries must be unique"):
        validate_draft(draft)
    assert original["cases"][1]["query"] != draft["cases"][1]["query"]
